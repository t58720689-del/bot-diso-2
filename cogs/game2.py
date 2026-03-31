from __future__ import annotations

import json
import random
import re
from pathlib import Path
# Game 2: Nối từ tiếng Việt
import certifi
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

_WORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "words.txt"
_CHANNEL_ID = 1488553449824976998
_NON_WORD = re.compile(r"[0-9@#$%^&*()+={}\[\]|\\<>/~`\"_]")
_SKIP_MIN_PLAYERS = 5
_STOP_MIN_PLAYERS = 5
_ADMIN_ROLE_ID = (1185158470958333953,1469581542841122918)


def _load_lexicon() -> set[str]:
    words: set[str] = set()
    if not _WORDS_PATH.is_file():
        logger.warning("[GAME2] words file not found: %s", _WORDS_PATH)
        return words
    try:
        raw = _WORDS_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[GAME2] cannot read %s: %s", _WORDS_PATH, e)
        return words
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            w = json.loads(line).get("text", "").strip()
        except (json.JSONDecodeError, AttributeError):
            continue
        if w:
            n = _normalize(w)
            if n:
                words.add(n)
    logger.info("[GAME2] loaded %d words from %s", len(words), _WORDS_PATH)
    return words


def _normalize(text: str) -> str:
    s = text.lower().strip().replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def _first_syl(word: str) -> str:
    return word.split()[0] if word else ""


def _last_syl(word: str) -> str:
    return word.split()[-1] if word else ""


def _parse_input(content: str) -> str | None:
    s = content.strip()
    if not s or len(s) > 80:
        return None
    if _NON_WORD.search(s):
        return None
    n = _normalize(s)
    if not n or len(n) < 2 or len(n.split()) < 2 or len(n.split()) > 7:
        return None
    return n


class Game2(commands.Cog):
    """Nối từ tiếng Việt — từ tiếp phải bắt đầu bằng âm tiết cuối của từ trước."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lexicon: set[str] = set()
        self._mongo: AsyncIOMotorClient | None = None
        self._db = None

    async def cog_load(self) -> None:
        self._lexicon = _load_lexicon()
        if config.MONGO_URI:
            self._mongo = AsyncIOMotorClient(config.MONGO_URI, tlsCAFile=certifi.where())
            self._db = self._mongo[config.MONGO_DB_NAME_GAME2]
            await self._db.game2_used_words.create_index(
                [("channel_id", 1), ("word", 1)], unique=True
            )
            await self._db.game2_scores.create_index(
                [("channel_id", 1), ("user_id", 1)], unique=True
            )
            await self._db.game2_sessions.create_index("channel_id", unique=True)
            await self._db.game2_custom_words.create_index("word", unique=True)
            custom = await self._db.game2_custom_words.find(
                {}, {"word": 1, "_id": 0}
            ).to_list(length=None)
            count = 0
            for doc in custom:
                w = doc.get("word", "")
                if w and w not in self._lexicon:
                    self._lexicon.add(w)
                    count += 1
            if count:
                logger.info("[GAME2] loaded %d custom words from MongoDB", count)
            logger.info("[GAME2] MongoDB connected — db=%s", config.MONGO_DB_NAME_GAME2)
        else:
            logger.warning("[GAME2] MONGO_URI not set — data will NOT be persisted")

    async def cog_unload(self) -> None:
        if self._mongo:
            self._mongo.close()

    # ── MongoDB helpers ───────────────────────────────────────────

    async def _get_session(self, ch: int) -> dict:
        if self._db is None:
            return {"channel_id": ch, "active": False, "last_word": None,
                    "skip_votes": [], "players": []}
        doc = await self._db.game2_sessions.find_one({"channel_id": ch})
        if doc:
            return doc
        default = {"channel_id": ch, "active": False, "last_word": None,
                    "skip_votes": [], "stop_votes": [], "players": []}
        await self._db.game2_sessions.insert_one(default)
        return default

    async def _update_session(self, ch: int, **fields) -> None:
        if self._db is None:
            return
        await self._db.game2_sessions.update_one(
            {"channel_id": ch}, {"$set": fields}, upsert=True
        )

    async def _reset_session(self, ch: int) -> None:
        if self._db is None:
            return
        await self._db.game2_sessions.update_one(
            {"channel_id": ch},
            {"$set": {"active": False, "last_word": None,
                      "skip_votes": [], "stop_votes": [], "players": []}},
            upsert=True,
        )
        await self._db.game2_used_words.delete_many({"channel_id": ch})
        await self._db.game2_scores.delete_many({"channel_id": ch})

    async def _start_session(self, ch: int) -> None:
        await self._reset_session(ch)
        await self._update_session(ch, active=True)

    async def _is_word_used(self, ch: int, word: str) -> bool:
        if self._db is None:
            return False
        return await self._db.game2_used_words.find_one(
            {"channel_id": ch, "word": word}
        ) is not None

    async def _add_used_word(self, ch: int, word: str) -> None:
        if self._db is None:
            return
        try:
            await self._db.game2_used_words.insert_one(
                {"channel_id": ch, "word": word}
            )
        except Exception:
            pass

    async def _count_used(self, ch: int) -> int:
        if self._db is None:
            return 0
        return await self._db.game2_used_words.count_documents({"channel_id": ch})

    async def _add_score(self, ch: int, user_id: int) -> None:
        if self._db is None:
            return
        await self._db.game2_scores.update_one(
            {"channel_id": ch, "user_id": user_id},
            {"$inc": {"score": 1}},
            upsert=True,
        )

    async def _get_scores(self, ch: int) -> list[dict]:
        if self._db is None:
            return []
        cursor = self._db.game2_scores.find(
            {"channel_id": ch}
        ).sort("score", -1)
        return await cursor.to_list(length=None)

    async def _add_player(self, ch: int, user_id: int) -> None:
        if self._db is None:
            return
        await self._db.game2_sessions.update_one(
            {"channel_id": ch},
            {"$addToSet": {"players": user_id}},
        )

    async def _get_players(self, ch: int) -> list[int]:
        sess = await self._get_session(ch)
        return sess.get("players", [])

    async def _add_skip_vote(self, ch: int, user_id: int) -> int:
        if self._db is None:
            return 0
        await self._db.game2_sessions.update_one(
            {"channel_id": ch},
            {"$addToSet": {"skip_votes": user_id}},
        )
        sess = await self._get_session(ch)
        return len(sess.get("skip_votes", []))

    async def _has_skip_voted(self, ch: int, user_id: int) -> bool:
        sess = await self._get_session(ch)
        return user_id in sess.get("skip_votes", [])

    async def _clear_skip_votes(self, ch: int) -> None:
        await self._update_session(ch, skip_votes=[])

    async def _add_stop_vote(self, ch: int, user_id: int) -> int:
        if self._db is None:
            return 0
        await self._db.game2_sessions.update_one(
            {"channel_id": ch},
            {"$addToSet": {"stop_votes": user_id}},
        )
        sess = await self._get_session(ch)
        return len(sess.get("stop_votes", []))

    async def _has_stop_voted(self, ch: int, user_id: int) -> bool:
        sess = await self._get_session(ch)
        return user_id in sess.get("stop_votes", [])

    async def _get_all_used_words(self, ch: int) -> set[str]:
        if self._db is None:
            return set()
        cursor = self._db.game2_used_words.find(
            {"channel_id": ch}, {"word": 1, "_id": 0}
        )
        docs = await cursor.to_list(length=None)
        return {d["word"] for d in docs}

    # ── display helpers ───────────────────────────────────────────

    def _in_lexicon(self, word: str) -> bool:
        return word in self._lexicon

    @staticmethod
    def _name(guild: discord.Guild | None, uid: int) -> str:
        if guild:
            m = guild.get_member(uid)
            if m:
                return m.display_name
        return f"User {uid}"

    async def _leaderboard(
        self,
        guild: discord.Guild | None,
        ch: int,
        *,
        limit: int = 15,
        title: str = "🏆 **Bảng xếp hạng** (phiên hiện tại)",
    ) -> str:
        scores = await self._get_scores(ch)
        if not scores:
            return f"{title}\n_Chưa có lượt hợp lệ nào._"
        ranked = sorted(
            scores,
            key=lambda x: (-x["score"], self._name(guild, x["user_id"]).lower()),
        )
        lines = [title, ""]
        for i, doc in enumerate(ranked[:limit], start=1):
            name = self._name(guild, doc["user_id"])
            lines.append(f"**{i}.** {name} — **{doc['score']}** từ")
        if len(ranked) > limit:
            lines.append(f"_… và {len(ranked) - limit} người khác_")
        return "\n".join(lines)

    @staticmethod
    def _guide_embed() -> discord.Embed:
        e = discord.Embed(
            title="📖 Hướng dẫn chơi Nối Từ Tiếng Việt",
            color=discord.Color.green(),
        )
        e.add_field(
            name="🎯 Luật chơi",
            value=(
                "• Mỗi người gửi **một từ/cụm từ tiếng Việt** (tối thiểu **2 âm tiết**).\n"
                "• Từ tiếp theo phải **bắt đầu** bằng **âm tiết cuối** của từ trước.\n"
                "  VD: *xin chào* → *chào mừng* → *mừng rỡ* → …\n"
                "• **Không được lặp** từ đã dùng trong phiên.\n"
                "• Mỗi từ hợp lệ được **+1 điểm**."
            ),
            inline=False,
        )
        e.add_field(
            name="⌨️ Các lệnh",
            value=(
                "`!ntvstart [từ]` — Bắt đầu phiên mới (có thể kèm từ khởi đầu)\n"
                "`!ntvstop` — Vote kết thúc phiên (cần ≥ 5 người vote)\n"
                "`!ntvskip` — Vote bỏ qua từ hiện tại (cần ≥ 5 người vote)\n"
                "`!ntvstatus` — Xem trạng thái phiên\n"
                "`!ntvleaderboard` — Xem bảng xếp hạng phiên\n"
                "`!ntvadd <từ>` — Thêm từ mới vào từ điển (cần quyền)\n"
                "`!ntvhelp` — Xem lại hướng dẫn này"
            ),
            inline=False,
        )
        e.add_field(
            name="💡 Mẹo",
            value=(
                "• Gõ đúng **dấu tiếng Việt** để từ được nhận.\n"
                "• Dùng `!ntvstatus` nếu quên từ hiện tại.\n"
                "• Mỗi tin nhắn chỉ nên chứa **đúng một từ/cụm từ**."
            ),
            inline=False,
        )
        e.set_footer(text="Chúc các bạn chơi vui vẻ! 🎉")
        return e

    async def _check_dead_end(
        self,
        channel: discord.abc.Messageable,
        ch_id: int,
        guild: discord.Guild | None,
    ) -> None:
        sess = await self._get_session(ch_id)
        last_word = sess.get("last_word")
        if last_word is None:
            return

        used = await self._get_all_used_words(ch_id)
        tail = _last_syl(last_word)
        has_next = any(
            w for w in self._lexicon
            if w not in used and len(w.split()) >= 2 and _first_syl(w) == tail
        )
        if has_next:
            return

        parts: list[str] = [f"🚫 Không còn từ nào bắt đầu bằng **`{tail}`** — hết từ nối!"]

        scores = await self._get_scores(ch_id)
        if scores:
            winner = scores[0]
            winner_name = self._name(guild, winner["user_id"])
            parts.append(
                f"\n🎉 **Người chiến thắng: {winner_name}** với **{winner['score']}** từ!"
            )
            parts.append(
                await self._leaderboard(guild, ch_id, title="\n🏆 **Bảng xếp hạng**")
            )

        candidates = [
            w for w in self._lexicon if w not in used and len(w.split()) >= 2
        ]
        if candidates:
            new_word = random.choice(candidates)
            await self._add_used_word(ch_id, new_word)
            await self._update_session(ch_id, last_word=new_word, skip_votes=[])
            new_tail = _last_syl(new_word)
            used_count = await self._count_used(ch_id)
            parts.append(
                f"\n🔄 Bot chọn từ mới: **{new_word}** — từ tiếp theo phải bắt đầu bằng **`{new_tail}`**.\n"
                f"Đã dùng **{used_count}** từ."
            )
        else:
            parts.append("\n⚠️ Từ điển đã hết từ! Dùng `!ntvstart` để bắt đầu phiên mới.")
            await self._update_session(ch_id, active=False)

        try:
            await channel.send("\n".join(parts))
        except discord.HTTPException:
            pass

    # ── listeners ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        ch = self.bot.get_channel(_CHANNEL_ID)
        if ch is None:
            return
        embed = self._guide_embed()
        embed.title = "🤖 Bot đã khởi động — Hướng dẫn Nối Từ Tiếng Việt"
        try:
            await ch.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.channel.id != _CHANNEL_ID:
            return
        content = (message.content or "").strip()
        if not content or content.startswith("!"):
            return

        word = _parse_input(content)
        if word is None:
            return

        ch_id = message.channel.id
        sess = await self._get_session(ch_id)
        if not sess.get("active"):
            return

        await self._add_player(ch_id, message.author.id)

        last_word = sess.get("last_word")
        if last_word is None:
            if not self._in_lexicon(word):
                try:
                    await message.reply(
                        f"❌ **{content}** không có trong từ điển. Thử từ khác.",
                        delete_after=15,
                    )
                except discord.HTTPException:
                    pass
                return
            await self._add_used_word(ch_id, word)
            await self._update_session(ch_id, last_word=word, skip_votes=[])
            await self._add_score(ch_id, message.author.id)
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
            await self._check_dead_end(message.channel, ch_id, message.guild)
            return

        need = _last_syl(last_word)
        got = _first_syl(word)
        if got != need:
            try:
                await message.reply(
                    f"❌ Từ phải bắt đầu bằng **`{need}`** (theo từ trước: **{last_word}**).",
                    delete_after=12,
                )
            except discord.HTTPException:
                pass
            return

        if await self._is_word_used(ch_id, word):
            try:
                await message.reply(
                    "❌ Từ này **đã được dùng** trong phiên. Thử từ khác.",
                    delete_after=12,
                )
            except discord.HTTPException:
                pass
            return

        if not self._in_lexicon(word):
            try:
                await message.reply(
                    f"❌ **{content}** không có trong từ điển. Thử từ khác.",
                    delete_after=15,
                )
            except discord.HTTPException:
                pass
            return

        await self._add_used_word(ch_id, word)
        await self._update_session(ch_id, last_word=word, skip_votes=[])
        await self._add_score(ch_id, message.author.id)
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass
        await self._check_dead_end(message.channel, ch_id, message.guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.is_timed_out() or not after.is_timed_out():
            return
        if self._db is None:
            return
        cursor = self._db.game2_sessions.find({"active": True}, {"channel_id": 1})
        active_sessions = await cursor.to_list(length=None)
        for doc in active_sessions:
            ch_id = doc["channel_id"]
            players = await self._get_players(ch_id)
            if after.id not in players:
                continue
            channel = self.bot.get_channel(ch_id)
            try:
                await after.timeout(None, reason="Auto-remove: đang chơi nối từ TV")
                logger.info(
                    "[GAME2] Auto-removed timeout for %s (id=%s) in channel %s",
                    after.name, after.id, ch_id,
                )
                if channel:
                    try:
                        await channel.send(
                            f"🔓 {after.mention} vừa bị timeout nhưng **đang chơi nối từ** "
                            "nên đã được gỡ tự động. Cẩn thận hơn nhé! 😅",
                        )
                    except discord.HTTPException:
                        pass
            except discord.Forbidden:
                logger.warning(
                    "[GAME2] No permission to remove timeout for %s (id=%s)",
                    after.name, after.id,
                )
            except discord.HTTPException as e:
                logger.error("[GAME2] Failed to remove timeout for %s: %s", after.name, e)
            break

    # ── commands ───────────────────────────────────────────────────

    @commands.command(name="ntvhelp", aliases=["ntvguide"])
    async def ntvhelp(self, ctx: commands.Context):
        """Xem hướng dẫn chơi nối từ tiếng Việt."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        await ctx.send(embed=self._guide_embed(), delete_after=120)

    @commands.command(name="ntvstart", aliases=["ntvs", "ntv"])
    async def ntvstart(self, ctx: commands.Context, *, word: str = None):
        """Bắt đầu phiên nối từ tiếng Việt mới. VD: `!ntvstart xin chào`"""
        if ctx.channel.id != _CHANNEL_ID:
            return
        ch_id = ctx.channel.id
        await self._start_session(ch_id)
        await ctx.send(embed=self._guide_embed())

        if word:
            n = _normalize(word)
            if not n or len(n.split()) < 2:
                await self._update_session(ch_id, active=False)
                await ctx.send(
                    "❌ Từ khởi đầu phải có **ít nhất 2 âm tiết** (VD: *xin chào*).",
                    delete_after=15,
                )
                return
            if not self._in_lexicon(n):
                await self._update_session(ch_id, active=False)
                await ctx.send(
                    f"❌ **{word}** không có trong từ điển. Thử từ khác.",
                    delete_after=18,
                )
                return
            await self._add_used_word(ch_id, n)
            await self._update_session(ch_id, last_word=n, skip_votes=[])
            await self._add_score(ch_id, ctx.author.id)
            await self._add_player(ch_id, ctx.author.id)
            tail = _last_syl(n)
            used_count = await self._count_used(ch_id)
            await ctx.send(
                f"🔤 Phiên mới! Từ đầu: **{n}** — từ tiếp theo phải bắt đầu bằng **`{tail}`**.\n"
                f"Không được lặp từ đã dùng ({used_count} từ). Xem điểm: `!ntvleaderboard`.",
            )
        else:
            await ctx.send(
                "🔤 Phiên mới (chưa có từ). Gửi **một từ/cụm từ tiếng Việt** để bắt đầu.\n"
                "Mỗi từ tiếp theo phải bắt đầu bằng **âm tiết cuối** của từ trước; "
                "**không lặp** từ đã dùng.\n"
                f"Từ điển có **{len(self._lexicon):,}** từ. "
                "Mỗi từ hợp lệ +1 điểm — `!ntvleaderboard` xem bảng phiên.",
            )

    @commands.command(name="ntvstop")
    async def ntvstop(self, ctx: commands.Context):
        """Vote kết thúc phiên nối từ — cần ≥ 5 người dùng lệnh này."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        ch_id = ctx.channel.id
        sess = await self._get_session(ch_id)
        if not sess.get("active"):
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=12,
            )
            return

        if await self._has_stop_voted(ch_id, ctx.author.id):
            await ctx.send("⚠️ Bạn đã vote dừng rồi. Chờ người khác vote thêm.", delete_after=10)
            return

        current = await self._add_stop_vote(ch_id, ctx.author.id)
        needed = _STOP_MIN_PLAYERS

        if current < needed:
            await ctx.send(
                f"🗳️ **{ctx.author.display_name}** vote dừng phiên! "
                f"(**{current}/{needed}** — cần thêm **{needed - current}** vote nữa)",
                delete_after=20,
            )
            return

        board = None
        scores = await self._get_scores(ch_id)
        if scores:
            board = await self._leaderboard(
                ctx.guild, ch_id, title="🏆 **Kết thúc phiên** — bảng xếp hạng",
            )
        await self._reset_session(ch_id)
        msg = f"⏹️ Đủ **{needed}** vote — đã kết thúc phiên nối từ tiếng Việt."
        if board:
            msg += "\n\n" + board
        await ctx.send(msg, delete_after=45 if board else 12)

    @commands.command(name="ntvstatus")
    async def ntvstatus(self, ctx: commands.Context):
        """Xem trạng thái phiên nối từ tiếng Việt."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        ch_id = ctx.channel.id
        sess = await self._get_session(ch_id)
        if not sess.get("active"):
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=12,
            )
            return
        last_word = sess.get("last_word")
        used_count = await self._count_used(ch_id)
        if last_word is None:
            await ctx.send(
                f"Đang chờ **từ đầu tiên**. Đã dùng: {used_count} từ.", delete_after=12,
            )
            return
        tail = _last_syl(last_word)
        await ctx.send(
            f"Từ hiện tại: **{last_word}** — từ tiếp theo bắt đầu bằng **`{tail}`**.\n"
            f"Đã dùng **{used_count}** từ (không được lặp). "
            "Bảng điểm: `!ntvleaderboard`.",
            delete_after=20,
        )

    @commands.command(name="ntvskip")
    async def ntvskip(self, ctx: commands.Context):
        """Vote bỏ qua từ hiện tại — cần ≥ 5 người dùng lệnh này."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        ch_id = ctx.channel.id
        sess = await self._get_session(ch_id)
        if not sess.get("active"):
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=12,
            )
            return
        if sess.get("last_word") is None:
            await ctx.send("⚠️ Chưa có từ nào để skip. Hãy gửi từ đầu tiên.", delete_after=12)
            return

        if await self._has_skip_voted(ch_id, ctx.author.id):
            await ctx.send("⚠️ Bạn đã vote skip rồi. Chờ người khác vote thêm.", delete_after=10)
            return

        current = await self._add_skip_vote(ch_id, ctx.author.id)
        needed = _SKIP_MIN_PLAYERS

        if current < needed:
            await ctx.send(
                f"🗳️ **{ctx.author.display_name}** vote skip! "
                f"(**{current}/{needed}** — cần thêm **{needed - current}** vote nữa)",
                delete_after=20,
            )
            return

        used = await self._get_all_used_words(ch_id)
        candidates = [w for w in self._lexicon if w not in used and len(w.split()) >= 2]
        if not candidates:
            await ctx.send("⚠️ Không còn từ nào trong từ điển để chọn!", delete_after=15)
            return
        new_word = random.choice(candidates)
        await self._add_used_word(ch_id, new_word)
        await self._update_session(ch_id, last_word=new_word, skip_votes=[])
        tail = _last_syl(new_word)
        used_count = await self._count_used(ch_id)
        await ctx.send(
            f"⏭️ Đủ **{needed}** vote — đã skip! Từ mới: **{new_word}** — "
            f"từ tiếp theo phải bắt đầu bằng **`{tail}`**.\n"
            f"Đã dùng **{used_count}** từ.",
        )

    @commands.command(name="ntvleaderboard", aliases=["ntvrank", "ntvtop"])
    async def ntvleaderboard(self, ctx: commands.Context):
        """Bảng xếp hạng phiên nối từ tiếng Việt hiện tại."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        ch_id = ctx.channel.id
        sess = await self._get_session(ch_id)
        if not sess.get("active"):
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=15,
            )
            return
        await ctx.send(await self._leaderboard(ctx.guild, ch_id), delete_after=60)

    @commands.command(name="ntvadd")
    async def ntvadd(self, ctx: commands.Context, *, word: str = None):
        """Thêm từ mới vào từ điển. Chỉ dành cho người có role đặc biệt."""
        if not any(r.id in _ADMIN_ROLE_ID for r in ctx.author.roles):
            await ctx.send(
                "❌ Bạn không có quyền sử dụng lệnh này.", delete_after=10,
            )
            return
        if not word:
            await ctx.send(
                "❌ Vui lòng nhập từ cần thêm. VD: `!ntvadd xin chào`", delete_after=10,
            )
            return

        n = _normalize(word)
        if not n or len(n.split()) < 2:
            await ctx.send(
                "❌ Từ phải có ít nhất **2 âm tiết**. VD: `!ntvadd xin chào`",
                delete_after=10,
            )
            return

        if n in self._lexicon:
            await ctx.send(f"⚠️ **{n}** đã có trong từ điển rồi.", delete_after=10)
            return

        if self._db is not None:
            try:
                await self._db.game2_custom_words.insert_one({"word": n})
            except Exception as e:
                logger.error("[GAME2] Failed to save custom word to MongoDB: %s", e)
                await ctx.send(f"❌ Lỗi khi lưu từ vào database: {e}", delete_after=15)
                return
        else:
            try:
                with open(_WORDS_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"text": n}, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.error("[GAME2] Failed to write word to %s: %s", _WORDS_PATH, e)
                await ctx.send(f"❌ Lỗi khi ghi file: {e}", delete_after=15)
                return

        self._lexicon.add(n)
        await ctx.send(
            f"✅ Đã thêm **{n}** vào từ điển. Tổng: **{len(self._lexicon):,}** từ."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Game2(bot))
