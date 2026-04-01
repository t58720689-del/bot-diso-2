import asyncio
import re
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

_ENGLISH_WORD = re.compile(r"^[A-Za-z]{2,}$")
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "word_chain_words.txt"
_DICT_CACHE_MAX = 4000
_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"

#1486759905431130175




def _word_chain_channels():
    ids = getattr(config, "WORD_CHAIN_CHANNEL_IDS", None) or []
    return set(ids)


def _dictionary_check_enabled() -> bool:
    return bool(getattr(config, "WORD_CHAIN_DICTIONARY_CHECK", True))


def _parse_single_english_word(content: str) -> str | None:
    s = (content or "").strip()
    if not s or any(c.isspace() for c in s):
        return None
    if not _ENGLISH_WORD.fullmatch(s):
        return None
    return s.lower()


def _load_lexicon_from_file() -> set[str] | None:
    if not _LEXICON_PATH.is_file():
        return None
    try:
        text = _LEXICON_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Word chain: cannot read %s: %s", _LEXICON_PATH, e)
        return None
    words = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if _ENGLISH_WORD.fullmatch(low):
            words.add(low)
    if not words:
        return None
    logger.info("Word chain: loaded %s words from %s", len(words), _LEXICON_PATH)
    return words


class Game1(commands.Cog):
    """Nối từ tiếng Anh: chữ đầu = chữ cuối từ trước; không lặp từ; có thể kiểm tra từ điển."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lexicon: set[str] | None = None
        self._dict_cache: dict[str, bool] = {}
        self._http: aiohttp.ClientSession | None = None
        self._mongo: AsyncIOMotorClient | None = None
        self._db = None
        self._last_active: dict[tuple[int, int], float] = {}
        self._active_grace_sec: float = 30.0
        self._channel_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self._lexicon = _load_lexicon_from_file()
        need_http = self._lexicon is None and _dictionary_check_enabled()
        if need_http:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5),
                headers={"User-Agent": "DiscordBot-WordChain/1.0"},
            )
            logger.info("Word chain: dictionary via API (no %s)", _LEXICON_PATH.name)

        if config.MONGO_URI:
            self._mongo = AsyncIOMotorClient(
                config.MONGO_URI,
                tls=True,
                tlsAllowInvalidCertificates=True,
                serverSelectionTimeoutMS=10_000,
            )
            self._db = self._mongo[config.MONGO_DB_NAME]
            await self._db.game1_sessions.create_index("channel_id", unique=True)
            await self._db.game1_used_words.create_index(
                [("channel_id", 1), ("word", 1)], unique=True
            )
            await self._db.game1_scores.create_index(
                [("channel_id", 1), ("user_id", 1)], unique=True
            )
            logger.info("[GAME1] MongoDB connected — db=%s", config.MONGO_DB_NAME)
        else:
            logger.warning("[GAME1] MONGO_URI not set — data will NOT be persisted")

    async def cog_unload(self) -> None:
        if self._http:
            await self._http.close()
            self._http = None
        if self._mongo:
            self._mongo.close()

    # ── MongoDB helpers ───────────────────────────────────────────

    async def _get_session(self, ch: int) -> dict:
        if self._db is None:
            return {"channel_id": ch, "active": False, "last_word": None, "players": []}
        doc = await self._db.game1_sessions.find_one({"channel_id": ch})
        if doc:
            return doc
        default = {"channel_id": ch, "active": False, "last_word": None, "players": []}
        await self._db.game1_sessions.insert_one(default)
        return default

    async def _update_session(self, ch: int, **fields) -> None:
        if self._db is None:
            return
        await self._db.game1_sessions.update_one(
            {"channel_id": ch}, {"$set": fields}, upsert=True
        )

    async def _reset_session(self, ch: int) -> None:
        if self._db is None:
            return
        await self._db.game1_sessions.update_one(
            {"channel_id": ch},
            {"$set": {"active": False, "last_word": None, "players": []}},
            upsert=True,
        )
        await self._db.game1_used_words.delete_many({"channel_id": ch})
        self._last_active = {
            k: v for k, v in self._last_active.items() if k[0] != ch
        }

    async def _start_session(self, ch: int) -> None:
        await self._reset_session(ch)
        await self._update_session(ch, active=True)

    async def _is_word_used(self, ch: int, word: str) -> bool:
        if self._db is None:
            return False
        return await self._db.game1_used_words.find_one(
            {"channel_id": ch, "word": word}
        ) is not None

    async def _add_used_word(self, ch: int, word: str) -> None:
        if self._db is None:
            return
        try:
            await self._db.game1_used_words.insert_one({"channel_id": ch, "word": word})
        except Exception:
            pass

    async def _count_used(self, ch: int) -> int:
        if self._db is None:
            return 0
        return await self._db.game1_used_words.count_documents({"channel_id": ch})

    async def _add_score(self, ch: int, user_id: int) -> None:
        if self._db is None:
            return
        await self._db.game1_scores.update_one(
            {"channel_id": ch, "user_id": user_id},
            {"$inc": {"score": 1}},
            upsert=True,
        )

    async def _get_scores(self, ch: int) -> list[dict]:
        if self._db is None:
            return []
        cursor = self._db.game1_scores.find({"channel_id": ch}).sort("score", -1)
        return await cursor.to_list(length=None)

    async def _add_player(self, ch: int, user_id: int) -> None:
        if self._db is None:
            return
        await self._db.game1_sessions.update_one(
            {"channel_id": ch},
            {"$addToSet": {"players": user_id}},
        )

    async def _get_players(self, ch: int) -> list[int]:
        sess = await self._get_session(ch)
        return sess.get("players", [])

    def _lock_for(self, channel_id: int) -> asyncio.Lock:
        lock = self._channel_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._channel_locks[channel_id] = lock
        return lock

    # ── Milestone notification ────────────────────────────────────

    async def _check_milestone(self, channel: discord.abc.Messageable, count: int) -> None:
        if count > 0 and count % 100 == 0:
            try:
                await channel.send(
                    f"🎉 **Amazing! {count} words played!**\n"
                    f"Dùng lệnh `!wcleaderboard` để xem bảng xếp hạng!"
                )
            except discord.HTTPException:
                pass

    # ── Dictionary check ──────────────────────────────────────────

    def _trim_dict_cache(self) -> None:
        if len(self._dict_cache) > _DICT_CACHE_MAX:
            self._dict_cache.clear()

    async def _is_valid_english_word(self, w: str) -> tuple[bool, str | None]:
        """(hợp lệ, mã lỗi ngắn cho thông báo — None nếu hợp lệ)."""
        if not _dictionary_check_enabled():
            return True, None

        if self._lexicon is not None:
            if w in self._lexicon:
                return True, None
            return False, "lexicon"

        if w in self._dict_cache:
            return (True, None) if self._dict_cache[w] else (False, "api_404")

        if self._http is None:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5),
                headers={"User-Agent": "DiscordBot-WordChain/1.0"},
            )

        url = _API_URL.format(word=quote(w, safe=""))
        try:
            async with self._http.get(url) as resp:
                if resp.status == 200:
                    self._dict_cache[w] = True
                    self._trim_dict_cache()
                    return True, None
                if resp.status == 404:
                    self._dict_cache[w] = False
                    self._trim_dict_cache()
                    return False, "api_404"
                logger.warning("Word chain dictionary API status %s for %r", resp.status, w)
                return False, "api_other"
        except asyncio.TimeoutError:
            return False, "network"
        except aiohttp.ClientError as e:
            logger.warning("Word chain dictionary request failed: %s", e)
            return False, "network"

    # ── Embeds / formatting ───────────────────────────────────────

    @staticmethod
    def _build_guide_embed() -> discord.Embed:
        embed = discord.Embed(
            title="📖 Hướng dẫn chơi Nối Từ Tiếng Anh",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="🎯 Luật chơi",
            value=(
                "• Mỗi người gửi **một từ tiếng Anh** (chỉ chữ cái A–Z, ≥ 2 ký tự).\n"
                "• Từ tiếp theo phải **bắt đầu** bằng **chữ cái cuối** của từ trước.\n"
                "• **Không được lặp** từ đã dùng trong phiên.\n"
                "• Mỗi từ hợp lệ được **+1 điểm**."
            ),
            inline=False,
        )
        embed.add_field(
            name="⌨️ Các lệnh",
            value=(
                "`!wcstart [từ]` — Bắt đầu phiên mới (có thể kèm từ khởi đầu)\n"
                "`!wcstop` — Kết thúc phiên hiện tại\n"
                "`!wcstatus` — Xem trạng thái phiên\n"
                "`!wcleaderboard` — Xem bảng xếp hạng phiên\n"
                "`!wchelp` — Xem lại hướng dẫn này"
            ),
            inline=False,
        )
        embed.add_field(
            name="💡 Mẹo",
            value=(
                "• Từ sẽ được kiểm tra qua từ điển — chỉ từ thật mới được chấp nhận.\n"
                "• Gõ duy nhất **một từ** trên mỗi tin nhắn.\n"
                "• Dùng `!wcstatus` nếu quên từ hiện tại."
            ),
            inline=False,
        )
        embed.set_footer(text="Chúc các bạn chơi vui vẻ! 🎉")
        return embed

    @staticmethod
    def _dict_error_message(w: str, code: str | None) -> str:
        if code == "lexicon":
            return (
                f"❌ **{w}** không có trong danh sách từ (`data/word_chain_words.txt`). "
                "Thêm từ vào file hoặc dùng từ khác."
            )
        if code == "api_404":
            return (
                f"❌ **{w}** không tìm thấy trong từ điển tiếng Anh (API). "
                "Có thể là lỗi chính tả, từ viết tắt, hoặc từ quá hiếm."
            )
        if code in ("network", "api_other"):
            return "⚠️ Không kiểm tra được từ điển (mạng hoặc lỗi API). Thử lại sau vài giây."
        return "❌ Từ không được chấp nhận."

    @staticmethod
    def _player_display_name(guild: discord.Guild | None, user_id: int) -> str:
        if guild:
            m = guild.get_member(user_id)
            if m:
                return m.display_name
        return f"User {user_id}"

    def _format_session_leaderboard(
        self,
        guild: discord.Guild | None,
        scores: list[dict],
        *,
        limit: int = 15,
        title: str = "🏆 **Bảng xếp hạng** (phiên hiện tại)",
    ) -> str:
        if not scores:
            return f"{title}\n_Chưa có lượt hợp lệ nào._"
        lines = [title, ""]
        for i, doc in enumerate(scores[:limit], start=1):
            uid = doc["user_id"]
            n = doc["score"]
            name = self._player_display_name(guild, uid)
            lines.append(f"**{i}.** {name} — **{n}** từ")
        if len(scores) > limit:
            lines.append(f"_… và {len(scores) - limit} người khác_")
        return "\n".join(lines)

    # ── Commands ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        channels = _word_chain_channels()
        if not channels:
            return
        embed = self._build_guide_embed()
        embed.title = "🤖 Bot đã khởi động — Hướng dẫn Nối Từ Tiếng Anh"
        for ch_id in channels:
            channel = self.bot.get_channel(ch_id)
            if channel is not None:
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

    @commands.command(name="wchelp", aliases=["wordhelp", "wcguide"])
    async def wchelp(self, ctx: commands.Context):
        """Xem hướng dẫn chơi nối từ tiếng Anh."""
        if ctx.channel.id not in _word_chain_channels():
            return
        await ctx.send(embed=self._build_guide_embed(), delete_after=120)

    @commands.command(name="wcstart", aliases=["wordstart", "wc"])
    async def wcstart(self, ctx: commands.Context, *, word: str = None):
        """Bắt đầu phiên mới. Có thể kèm từ khởi đầu: `!wcstart apple` hoặc chỉ `!wcstart` rồi gửi một từ."""
        if ctx.channel.id not in _word_chain_channels():
            return
        ch = ctx.channel.id
        sess = await self._get_session(ch)
        if sess.get("active"):
            await ctx.send(
                "⚠️ Đang có phiên nối từ! Dùng `!wcstop` để kết thúc trước khi bắt đầu phiên mới.",
                delete_after=15,
            )
            return
        await self._start_session(ch)
        await ctx.send(embed=self._build_guide_embed())
        if word:
            w = _parse_single_english_word(word)
            if not w:
                await self._update_session(ch, active=False)
                await ctx.send(
                    "❌ Từ khởi đầu không hợp lệ. Chỉ dùng **một từ** tiếng Anh (chữ cái A–Z), tối thiểu 2 ký tự.",
                    delete_after=15,
                )
                return
            ok, err = await self._is_valid_english_word(w)
            if not ok:
                await self._update_session(ch, active=False)
                await ctx.send(self._dict_error_message(w, err), delete_after=18)
                return
            await self._update_session(ch, last_word=w)
            await self._add_used_word(ch, w)
            await self._add_score(ch, ctx.author.id)
            await self._add_player(ch, ctx.author.id)
            self._last_active[(ch, ctx.author.id)] = time.monotonic()
            used_count = await self._count_used(ch)
            await ctx.send(
                f"🔤 Phiên mới! Từ đầu: **{w}** — từ tiếp theo phải bắt đầu bằng **`{w[-1]}`**. "
                f"Trong phiên này **không được lặp lại** từ đã dùng ({used_count} từ). "
                f"Xem điểm: `!wcleaderboard`.",
            )
            await self._check_milestone(ctx.channel, used_count)
        else:
            hint = ""
            if _dictionary_check_enabled():
                if self._lexicon is not None:
                    hint = f" Từ phải có trong `data/word_chain_words.txt` ({len(self._lexicon)} từ)."
                else:
                    hint = " Từ sẽ được kiểm tra qua từ điển (API)."
            await ctx.send(
                "🔤 Phiên mới (chưa có từ). Gửi **một dòng một từ** tiếng Anh (chữ cái, ≥2 ký tự) để bắt đầu. "
                "Sau đó mỗi từ phải bắt đầu bằng chữ cái cuối của từ trước; **không lặp** từ đã dùng. "
                "Mỗi từ hợp lệ +1 điểm — `!wcleaderboard` xem bảng phiên này."
                + hint,
            )

    @commands.command(name="wcstop", aliases=["wordstop"])
    async def wcstop(self, ctx: commands.Context):
        if ctx.channel.id not in _word_chain_channels():
            return
        ch = ctx.channel.id
        sess = await self._get_session(ch)
        if not sess.get("active"):
            await ctx.send("Chưa có phiên đang chơi.", delete_after=10)
            return
        players = sess.get("players", [])
        if len(players) < 5:
            await ctx.send(
                f"⚠️ Cần ít nhất **5 người chơi** mới được kết thúc phiên. "
                f"Hiện tại mới có **{len(players)}/5** người tham gia.",
                delete_after=15,
            )
            return
        scores = await self._get_scores(ch)
        board = None
        if scores:
            board = self._format_session_leaderboard(
                ctx.guild,
                scores,
                title="🏆 **Kết thúc phiên** — bảng xếp hạng",
            )
        await self._reset_session(ch)
        msg = "⏹️ Đã kết thúc phiên nối từ."
        if board:
            msg = msg + "\n\n" + board
        await ctx.send(msg, delete_after=45 if board else 12)

    @commands.command(name="wcstatus", aliases=["wordstatus"])
    async def wcstatus(self, ctx: commands.Context):
        if ctx.channel.id not in _word_chain_channels():
            return
        ch = ctx.channel.id
        sess = await self._get_session(ch)
        if not sess.get("active"):
            await ctx.send("Chưa có phiên đang chơi. Dùng `!wcstart` để bắt đầu.", delete_after=12)
            return
        last_word = sess.get("last_word")
        used_count = await self._count_used(ch)
        if last_word is None:
            await ctx.send(
                f"Đang chờ **từ đầu tiên**. Đã dùng: {used_count} từ.",
                delete_after=12,
            )
            return
        await ctx.send(
            f"Từ hiện tại: **{last_word}** — từ tiếp theo bắt đầu bằng **`{last_word[-1]}`**. "
            f"Đã dùng **{used_count}** từ (không được lặp). "
            f"Bảng điểm phiên: `!wcleaderboard`.",
            delete_after=20,
        )

    @commands.command(name="wcleaderboard", aliases=["wcrank", "wctop"])
    async def wcleaderboard(self, ctx: commands.Context):
        """Bảng xếp hạng theo số từ hợp lệ mỗi người trong phiên nối từ hiện tại."""
        if ctx.channel.id not in _word_chain_channels():
            return
        ch = ctx.channel.id
        sess = await self._get_session(ch)
        if not sess.get("active"):
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!wcstart` để bắt đầu; bảng xếp hạng chỉ tính trong một phiên.",
                delete_after=15,
            )
            return
        scores = await self._get_scores(ch)
        await ctx.send(self._format_session_leaderboard(ctx.guild, scores), delete_after=60)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.channel.id not in _word_chain_channels():
            return
        content = (message.content or "").strip()
        if not content or content.startswith("!"):
            return

        w = _parse_single_english_word(content)
        if w is None:
            return

        ch = message.channel.id
#1. lock channel
        async with self._lock_for(ch):
            sess = await self._get_session(ch)
            if not sess.get("active"):
                return

            await self._add_player(ch, message.author.id)

            last_word = sess.get("last_word")
            if last_word is None:
                ok, err = await self._is_valid_english_word(w)
                if not ok:
                    try:
                        await message.reply(self._dict_error_message(w, err), delete_after=15)
                    except discord.HTTPException:
                        pass
                    return
                if await self._is_word_used(ch, w):
                    try:
                        await message.reply(
                            "❌ Từ này **đã được dùng** trong phiên hiện tại. Thử từ khác.",
                            delete_after=12,
                        )
                    except discord.HTTPException:
                        pass
                    return
                await self._update_session(ch, last_word=w)
                await self._add_used_word(ch, w)
                await self._add_score(ch, message.author.id)
                self._last_active[(ch, message.author.id)] = time.monotonic()
                used_count = await self._count_used(ch)
                try:
                    await message.add_reaction("✅")
                except discord.HTTPException:
                    pass
                await self._check_milestone(message.channel, used_count)
                return

            need = last_word[-1]
            if w[0] != need:
                try:
                    await message.reply(
                        f"❌ Từ phải bắt đầu bằng **`{need}`** (theo từ trước: **{last_word}**).",
                        delete_after=12,
                    )
                except discord.HTTPException:
                    pass
                return

            if await self._is_word_used(ch, w):
                try:
                    await message.reply(
                        "❌ Từ này **đã được dùng** trong phiên hiện tại. Thử từ khác.",
                        delete_after=12,
                    )
                except discord.HTTPException:
                    pass
                return

            ok, err = await self._is_valid_english_word(w)
            if not ok:
                try:
                    await message.reply(self._dict_error_message(w, err), delete_after=15)
                except discord.HTTPException:
                    pass
                return

            await self._add_used_word(ch, w)
            await self._update_session(ch, last_word=w)
            await self._add_score(ch, message.author.id)
            self._last_active[(ch, message.author.id)] = time.monotonic()
            used_count = await self._count_used(ch)
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
            await self._check_milestone(message.channel, used_count)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Tự động gỡ timeout cho người chơi bị timeout trong lúc chơi nối từ."""
        if before.is_timed_out() or not after.is_timed_out():
            return

        game_channels = _word_chain_channels()
        if not game_channels:
            return

        for ch_id in game_channels:
            sess = await self._get_session(ch_id)
            if not sess.get("active"):
                continue
            if after.id not in sess.get("players", []):
                continue

            last_ts = self._last_active.get((ch_id, after.id))
            if last_ts is None or (time.monotonic() - last_ts) > self._active_grace_sec:
                continue

            channel = self.bot.get_channel(ch_id)
            try:
                await after.timeout(None, reason="Auto-remove: bị timeout trong lúc chơi nối từ")
                logger.info(
                    "[GAME1] Auto-removed timeout for %s (id=%s) in word-chain channel %s",
                    after.name, after.id, ch_id,
                )
                if channel is not None:
                    try:
                        await channel.send(
                            f"🔓 {after.mention} vừa bị timeout nhưng **đang chơi nối từ** nên đã được gỡ tự động. "
                            "Cẩn thận hơn nhé! 😅",
                        )
                    except discord.HTTPException:
                        pass
            except discord.Forbidden:
                logger.warning(
                    "[GAME1] Missing permission to remove timeout for %s (id=%s)",
                    after.name, after.id,
                )
            except discord.HTTPException as e:
                logger.error("[GAME1] Failed to remove timeout for %s: %s", after.name, e)
            break


async def setup(bot: commands.Bot):
    await bot.add_cog(Game1(bot))
