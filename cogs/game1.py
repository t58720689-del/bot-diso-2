from __future__ import annotations

import random
import re
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ── Cấu hình nối từ (chỉnh tại đây) ─────────────────────────────
# ID kênh Discord: Developer Mode → chuột phải kênh → Copy Channel ID
WORD_CHAIN_CHANNEL_IDS: list[int] = [1488571810978201723,1489908032786796544]
# True = chỉ chấp nhận từ có trong từ điển (file data/word_chain_words.txt nếu có, không thì API dictionaryapi.dev)
WORD_CHAIN_DICTIONARY_CHECK = True
# Số người phải gõ !wcstop (mỗi người 1 phiếu) thì phiên mới dừng
WCSTOP_VOTES_REQUIRED = 4
# ───────────────────────────────────────────────────────────────

_WORD_FILE = Path(__file__).resolve().parent.parent / "data" / "word_chain_words.txt"
_DICTIONARY_API = "https://api.dictionaryapi.dev/api/v2/entries/en/{}"
_WORD_RE = re.compile(r"^[a-z]+$")

# Gợi ý khi không có file từ (chỉ dùng cho !wchint, không thay API/file cho kiểm tra từ)
_HINT_FALLBACK = frozenset(
    """
    about above after again air all also an and animal another any apple around
    ask away baby back ball banana bank beach bear beautiful because been before
    begin behind believe below best better between big bird black blue boat book
    both box boy bread bring brother brown build bus butterfly cake call camel
    can car cat catch chair change chicken child city class clean clock close
    cloud cold color come computer could country course cover cow dance dark
    day decide deep desk dog door down draw dress drink drive duck eagle ear
    earth east easy eat egg elephant end evening eye face fact fair fall family
    far farm fast father feel few field find fire first fish five flower fly
    follow food foot forest forget form fox friend fruit full game garden gate
    girl give glass go gold good grape grass great green grow guitar hair half
    hand happy hard hat have head hear heart heavy help high hill history hit
    hold home horse hot hour house how hundred ice idea if important inch inside
    island job join juice jump just keep key king kitchen kite know lake land
    large last late laugh lead learn leave left leg lemon letter life light like
    line lion list little live long look love low machine make man many map
    march mark may mean meet men milk mind minute miss money month moon morning
    mother mountain mouse mouth move music must name near need nest never new
    next night nine north nose note nothing notice noun now number ocean of off
    often oil old on once one only open orange order other our out over own
    page paint paper parent park part pass past peace pen pencil person picture
    piece pig place plane plant play please point police pool poor possible
    pound power present pretty price print prison problem produce promise pull
    purple push put queen question quick quiet rabbit race radio rain raise
    reach read real red remember rest rice rich ride right ring river road rock
    room root rose round rule run sad safe sail salt same sand save say school
    science sea second see seed seem sell send sense sentence serve set seven
    several shall sheep ship shirt shoe shop short should show side sign silver
    simple sing sister sit six size skin sky sleep slow small smell smile snow
    so soft soldier solve some song soon sound south space speak special speed
    spell spend spoon spring square stand star start state stay steal step
    stick still stone stop store story street strong student study such sugar
    summer sun sunny supper supply support sure surface surprise sweet swim
    table tail take talk tall teacher team tell ten tent term test than thank
    that their them then there these they thick thin thing think third this
    those though thought thousand three through throw tiger time tiny tire to
    today together told tomato too took tool top toward town toy track train
    travel tree triangle trip truck true try tube turn turtle twelve twenty
    two under unit until up upon us use usual valley vegetable very view
    village visit voice vowel wait walk wall want warm wash watch water wave
    way weather week weight well west wet whale what wheel when where which
    while white who whole whose why wide wife wild wind window wine winter wire
    wise wish with woman wonder wood word work world would write wrong yard
    year yellow yes yesterday yet you young your zebra zero zoo
    """.split()
)


def _channel_ids() -> list[int]:
    return [int(x) for x in WORD_CHAIN_CHANNEL_IDS]


def _normalize_word(text: str) -> str | None:
    s = text.strip().lower()
    if not s or len(s) > 45:
        return None
    if not _WORD_RE.fullmatch(s):
        return None
    return s


def _load_word_file() -> set[str]:
    words: set[str] = set()
    if not _WORD_FILE.is_file():
        return words
    try:
        raw = _WORD_FILE.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[WORD_CHAIN] cannot read %s: %s", _WORD_FILE, e)
        return words
    for line in raw.splitlines():
        w = _normalize_word(line)
        if w:
            words.add(w)
    if words:
        logger.info("[WORD_CHAIN] loaded %d words from %s", len(words), _WORD_FILE)
    return words


def _word_chain_guide_embed() -> discord.Embed:
    e = discord.Embed(
        title="📖 Nối từ tiếng Anh — hướng dẫn lệnh",
        description=(
            "Luật: mỗi lần **một từ** (a–z), từ sau **bắt đầu** bằng **chữ cái cuối** của từ trước. "
            "Prefix **`!`**."
        ),
        color=discord.Color.blurple(),
    )
    e.add_field(
        name="Lệnh",
        value=(
            "`!wcstart` — Mở phiên mới **một lần** (xóa điểm / từ cũ); khi đang chơi phải `!wcstop` trước\n"
            "`!wcstart <từ>` — Giống trên, kèm từ khởi đầu\n"
            f"`!wcstop` — Vote dừng phiên — cần **{WCSTOP_VOTES_REQUIRED} người** (mỗi người 1 lần)\n"
            "_Khi phiên đang mở: **gõ một từ** (a–z) — bot reaction: ✅ đúng, ❌ sai; nối tiếp chuỗi thêm 🔥 + số bước._\n"
            "`!wchint` — Gợi ý từ (theo kho cục bộ)\n"
            "`!wchistory` — Các từ đã chơi gần đây\n"
            "`!wcscore [@user]` — Xem điểm\n"
            "`!wcleaderboard` / `!wclb` — Bảng xếp hạng\n"
            "`!wcstatus` — Trạng thái phiên / chữ cần nối"
        ),
        inline=False,
    )
    return e


class WordChain(commands.Cog):
    """Nối từ tiếng Anh: từ mới phải bắt đầu bằng chữ cái cuối của từ trước."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mongo: AsyncIOMotorClient | None = None
        self._db = None
        self._http: aiohttp.ClientSession | None = None
        self._file_lexicon: set[str] = set()
        self._api_ok: set[str] = set()
        self._api_bad: set[str] = set()
        self._local: dict[int, dict] = {}

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if getattr(self.bot, "_word_chain_guide_sent", False):
            return
        ids = _channel_ids()
        if not ids:
            return
        self.bot._word_chain_guide_sent = True
        embed = _word_chain_guide_embed()
        for cid in ids:
            ch = self.bot.get_channel(cid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(cid)
                except (discord.NotFound, discord.Forbidden, OSError) as e:
                    logger.warning("[WORD_CHAIN] không gửi được hướng dẫn — kênh %s: %s", cid, e)
                    continue
            if not isinstance(ch, discord.abc.Messageable):
                continue
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                logger.warning(
                    "[WORD_CHAIN] thiếu quyền gửi tin vào kênh %s (%s)", cid, ch
                )
            except discord.HTTPException as e:
                logger.warning("[WORD_CHAIN] lỗi gửi hướng dẫn kênh %s: %s", cid, e)

    async def cog_load(self) -> None:
        self._file_lexicon = _load_word_file()
        timeout = aiohttp.ClientTimeout(total=12, connect=5)
        self._http = aiohttp.ClientSession(timeout=timeout)
        if config.MONGO_URI:
            self._mongo = AsyncIOMotorClient(
                config.MONGO_URI,
                tls=True,
                tlsAllowInvalidCertificates=True,
                serverSelectionTimeoutMS=10_000,
            )
            self._db = self._mongo[config.MONGO_DB_NAME]
            await self._db.word_chain_sessions.create_index("channel_id", unique=True)
            await self._db.word_chain_used_words.create_index(
                [("channel_id", 1), ("word", 1)], unique=True
            )
            await self._db.word_chain_scores.create_index(
                [("channel_id", 1), ("user_id", 1)], unique=True
            )
            await self._db.word_chain_plays.create_index([("channel_id", 1), ("seq", 1)])
            logger.info("[WORD_CHAIN] MongoDB — db=%s", config.MONGO_DB_NAME)
        else:
            logger.warning("[WORD_CHAIN] MONGO_URI not set — dùng bộ nhớ tạm theo kênh")

    async def cog_unload(self) -> None:
        if self._http:
            await self._http.close()
            self._http = None
        if self._mongo:
            self._mongo.close()

    def _allowed_channel(self, channel_id: int) -> bool:
        ids = _channel_ids()
        return channel_id in ids if ids else True

    async def _session(self, ch: int) -> dict:
        if self._db is None:
            return self._local.setdefault(
                ch,
                {
                    "channel_id": ch,
                    "active": False,
                    "last_word": None,
                    "seq": 0,
                    "stop_votes": set(),
                },
            )
        doc = await self._db.word_chain_sessions.find_one({"channel_id": ch})
        if doc:
            return doc
        default = {
            "channel_id": ch,
            "active": False,
            "last_word": None,
            "seq": 0,
            "stop_votes": [],
        }
        await self._db.word_chain_sessions.insert_one(default)
        return default

    async def _set_session(self, ch: int, **fields) -> None:
        if self._db is None:
            s = self._local.setdefault(
                ch,
                {
                    "channel_id": ch,
                    "active": False,
                    "last_word": None,
                    "seq": 0,
                    "stop_votes": set(),
                },
            )
            s.update(fields)
            return
        await self._db.word_chain_sessions.update_one(
            {"channel_id": ch}, {"$set": fields}, upsert=True
        )

    async def _reset_game(self, ch: int) -> None:
        if self._db is None:
            self._local[ch] = {
                "channel_id": ch,
                "active": False,
                "last_word": None,
                "seq": 0,
                "stop_votes": set(),
            }
            return
        await self._db.word_chain_used_words.delete_many({"channel_id": ch})
        await self._db.word_chain_scores.delete_many({"channel_id": ch})
        await self._db.word_chain_plays.delete_many({"channel_id": ch})
        await self._set_session(
            ch, active=False, last_word=None, seq=0, stop_votes=[]
        )

    async def _add_stop_vote(self, ch: int, user_id: int) -> tuple[int, bool]:
        """(số phiếu sau thao tác, True nếu vừa thêm phiếu mới cho user_id)."""
        if self._db is None:
            s = self._local.setdefault(
                ch,
                {
                    "channel_id": ch,
                    "active": False,
                    "last_word": None,
                    "seq": 0,
                    "stop_votes": set(),
                },
            )
            votes = s.setdefault("stop_votes", set())
            if not isinstance(votes, set):
                votes = set(votes)
                s["stop_votes"] = votes
            if user_id in votes:
                return len(votes), False
            votes.add(user_id)
            return len(votes), True

        sess = await self._session(ch)
        votes = list(sess.get("stop_votes") or [])
        if user_id in votes:
            return len(votes), False
        votes.append(user_id)
        await self._set_session(ch, stop_votes=votes)
        return len(votes), True

    async def _word_used(self, ch: int, word: str) -> bool:
        if self._db is None:
            return word in self._local.setdefault(ch, {}).get("used", set())
        return (
            await self._db.word_chain_used_words.find_one(
                {"channel_id": ch, "word": word}
            )
            is not None
        )

    async def _mark_used(self, ch: int, word: str) -> None:
        if self._db is None:
            bucket = self._local.setdefault(ch, {})
            bucket.setdefault("used", set()).add(word)
            return
        try:
            await self._db.word_chain_used_words.insert_one(
                {"channel_id": ch, "word": word}
            )
        except Exception:
            pass

    async def _append_play(self, ch: int, user_id: int, word: str) -> None:
        if self._db is None:
            bucket = self._local.setdefault(ch, {})
            seq = bucket.get("seq", 0) + 1
            bucket["seq"] = seq
            bucket.setdefault("plays", []).append(
                {"seq": seq, "user_id": user_id, "word": word}
            )
            return
        sess = await self._session(ch)
        seq = int(sess.get("seq", 0)) + 1
        await self._set_session(ch, seq=seq)
        await self._db.word_chain_plays.insert_one(
            {"channel_id": ch, "seq": seq, "user_id": user_id, "word": word}
        )

    async def _add_score(self, ch: int, user_id: int) -> None:
        if self._db is None:
            bucket = self._local.setdefault(ch, {})
            scores = bucket.setdefault("scores", {})
            scores[user_id] = scores.get(user_id, 0) + 1
            return
        await self._db.word_chain_scores.update_one(
            {"channel_id": ch, "user_id": user_id},
            {"$inc": {"score": 1}},
            upsert=True,
        )

    async def _get_scores(self, ch: int) -> list[dict]:
        if self._db is None:
            bucket = self._local.get(ch, {})
            raw = bucket.get("scores", {})
            return [
                {"user_id": uid, "score": sc}
                for uid, sc in raw.items()
            ]
        cur = self._db.word_chain_scores.find({"channel_id": ch}).sort("score", -1)
        return await cur.to_list(length=None)

    async def _history(self, ch: int, limit: int = 40) -> list[dict]:
        if self._db is None:
            plays = self._local.get(ch, {}).get("plays", [])
            return sorted(plays, key=lambda x: x["seq"])[-limit:]
        cur = (
            self._db.word_chain_plays.find({"channel_id": ch})
            .sort("seq", -1)
            .limit(limit)
        )
        rows = await cur.to_list(length=None)
        return list(reversed(rows))

    async def _is_valid_word(self, word: str) -> bool:
        if not WORD_CHAIN_DICTIONARY_CHECK:
            return True
        if self._file_lexicon:
            return word in self._file_lexicon
        if word in self._api_ok:
            return True
        if word in self._api_bad:
            return False
        if self._http is None:
            return False
        url = _DICTIONARY_API.format(quote(word, safe=""))
        try:
            async with self._http.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        self._api_ok.add(word)
                        return True
                self._api_bad.add(word)
                return False
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning("[WORD_CHAIN] dictionary API error for %r: %s", word, e)
            return False

    def _hint_candidates(self, letter: str, used: set[str]) -> list[str]:
        letter = letter.lower()
        pool: set[str] = set()
        if self._file_lexicon:
            pool |= self._file_lexicon
        else:
            pool |= _HINT_FALLBACK
        return sorted(w for w in pool if w.startswith(letter) and w not in used)

    @staticmethod
    def _display_name(guild: discord.Guild | None, uid: int) -> str:
        if guild:
            m = guild.get_member(uid)
            if m:
                return m.display_name
        return str(uid)

    async def _leaderboard_text(
        self, guild: discord.Guild | None, ch: int, limit: int = 15
    ) -> str:
        scores = await self._get_scores(ch)
        if not scores:
            return "🏆 **Bảng xếp hạng**\n_Chưa có điểm trong phiên này._"
        ranked = sorted(
            scores,
            key=lambda x: (-x["score"], self._display_name(guild, x["user_id"]).lower()),
        )
        lines = ["🏆 **Bảng xếp hạng**", ""]
        for i, doc in enumerate(ranked[:limit], start=1):
            name = self._display_name(guild, doc["user_id"])
            lines.append(f"**{i}.** {name} — **{doc['score']}** điểm")
        if len(ranked) > limit:
            lines.append(f"_… và {len(ranked) - limit} người khác_")
        return "\n".join(lines)

    async def _gather_used(self, ch: int) -> set[str]:
        if self._db is None:
            return set(self._local.get(ch, {}).get("used", set()))
        cur = self._db.word_chain_used_words.find(
            {"channel_id": ch}, {"word": 1, "_id": 0}
        )
        docs = await cur.to_list(length=None)
        return {d["word"] for d in docs}

    def _message_starts_with_command_prefix(self, content: str) -> bool:
        s = content.lstrip()
        p = self.bot.command_prefix
        if isinstance(p, str):
            return s.startswith(p)
        return any(s.startswith(x) for x in p)

    async def _safe_add_reactions(self, message: discord.Message, *emojis: str) -> None:
        for e in emojis:
            try:
                await message.add_reaction(e)
            except discord.HTTPException as ex:
                logger.warning(
                    "[WORD_CHAIN] không thêm reaction %r: %s", e, ex
                )

    async def _send_word_error_notice(self, message: discord.Message, text: str) -> None:
        """Gửi thông báo lỗi từ, tự xóa sau 5 giây."""
        try:
            await message.channel.send(
                text,
                delete_after=5.0,
                allowed_mentions=discord.AllowedMentions(users=[message.author]),
            )
        except discord.HTTPException as ex:
            logger.warning("[WORD_CHAIN] không gửi được thông báo lỗi: %s", ex)

    async def _react_word_play(self, message: discord.Message, w: str) -> None:
        """Xử lý một từ: cập nhật game và phản hồi bằng reaction (✅ / ❌), giống bot mẫu."""
        ch = message.channel.id
        sess = await self._session(ch)
        if not sess.get("active"):
            return

        last = sess.get("last_word")
        continuing = bool(last)
        if last:
            need = last[-1]
            if w[0] != need:
                await self._safe_add_reactions(message, "❌")
                await self._send_word_error_notice(
                    message,
                    f"{message.author.mention} ❌ Từ phải bắt đầu bằng **`{need}`** (sau **{last}**).",
                )
                return

        if await self._word_used(ch, w):
            await self._safe_add_reactions(message, "❌")
            await self._send_word_error_notice(
                message,
                f"{message.author.mention} ❌ **`{w}`** đã được dùng trong phiên này.",
            )
            return

        if not await self._is_valid_word(w):
            await self._safe_add_reactions(message, "❌")
            await self._send_word_error_notice(
                message,
                f"{message.author.mention} ❌ **`{w}`** không có trong từ điển.",
            )
            return

        await self._mark_used(ch, w)
        await self._append_play(ch, message.author.id, w)
        await self._add_score(ch, message.author.id)
        await self._set_session(ch, last_word=w)

        sess_after = await self._session(ch)
        seq = int(sess_after.get("seq", 0))

        _KEYCAP = (
            "0️⃣",
            "1️⃣",
            "2️⃣",
            "3️⃣",
            "4️⃣",
            "5️⃣",
            "6️⃣",
            "7️⃣",
            "8️⃣",
            "9️⃣",
        )
        extras: list[str] = []
        if continuing:
            extras.append("🔥")
            # Giống bot mẫu: từ nối tiếp thêm số (thường là bước thứ seq+1 trên chuỗi)
            n = seq + 1
            if n <= 9:
                extras.append(_KEYCAP[n])
            elif n == 10:
                extras.append("🔟")

        await self._safe_add_reactions(message, "✅", *extras)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not self._allowed_channel(message.channel.id):
            return
        if message.attachments or message.stickers:
            return
        raw = message.content.strip()
        if not raw or self._message_starts_with_command_prefix(raw):
            return
        parts = raw.split()
        if len(parts) != 1:
            return
        w = _normalize_word(parts[0])
        if not w:
            return
        sess = await self._session(message.channel.id)
        if not sess.get("active"):
            return
        await self._react_word_play(message, w)

    # ── commands ─────────────────────────────────────────────────────

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        if not self._allowed_channel(ctx.channel.id):
            await ctx.send(
                "⛔ Lệnh nối từ chỉ dùng trong kênh được cấu hình trong `cogs/game1.py` (`WORD_CHAIN_CHANNEL_IDS`)."
            )
            return False
        return True

    @commands.command(name="wcstart")
    async def wcstart(self, ctx: commands.Context, start_word: str | None = None):
        """Bắt đầu phiên mới (xóa điểm / từ cũ). Có thể kèm từ khởi đầu."""
        ch = ctx.channel.id
        sess = await self._session(ch)
        if sess.get("active"):
            await ctx.send(
                "ℹ️ **Phiên đang chạy** — `!wcstart` chỉ dùng **một lần** mỗi phiên. "
                f"Dùng `!wcstop` để vote dừng (cần **{WCSTOP_VOTES_REQUIRED} người**), sau đó mới `!wcstart` lại."
            )
            return

        await self._reset_game(ch)
        await self._set_session(ch, active=True)

        if start_word is None:
            await ctx.send(
                "🎮 **Nối từ (English)** đã bắt đầu!\n"
                "Luật: mỗi lượt **một từ tiếng Anh** (chỉ chữ cái a–z). "
                "Từ sau phải **bắt đầu** bằng **chữ cái cuối** của từ trước.\n"
                "Gõ **một từ** (không prefix) trong kênh để đi từ đầu tiên."
            )
            return

        w = _normalize_word(start_word)
        if not w:
            await self._set_session(ch, active=False)
            await ctx.send("❌ Từ khởi đầu không hợp lệ (chỉ dùng chữ cái, không dấu cách).")
            return
        if not await self._is_valid_word(w):
            await self._set_session(ch, active=False)
            await ctx.send(f"❌ **`{w}`** không có trong từ điển.")
            return

        await self._mark_used(ch, w)
        await self._append_play(ch, ctx.author.id, w)
        await self._add_score(ch, ctx.author.id)
        await self._set_session(ch, active=True, last_word=w)
        await ctx.send(
            f"🎮 Đã bắt đầu với **{w}** (+1 cho {ctx.author.mention}).\n"
            f"Từ tiếp theo phải bắt đầu bằng **`{w[-1]}`** — gõ **một từ** trong kênh."
        )

    @commands.command(name="wcstop")
    async def wcstop(self, ctx: commands.Context):
        """Vote dừng phiên — đủ số người mới tắt (giữ lịch sử / điểm)."""
        ch = ctx.channel.id
        sess = await self._session(ch)
        if not sess.get("active"):
            await ctx.send("ℹ️ Hiện không có phiên đang chạy.")
            return

        n_req = WCSTOP_VOTES_REQUIRED
        count, is_new = await self._add_stop_vote(ch, ctx.author.id)
        if count < n_req:
            left = n_req - count
            if is_new:
                await ctx.send(
                    f"🗳️ **{ctx.author.display_name}** đã vote dừng phiên — **{count}/{n_req}**. "
                    f"Cần thêm **{left}** người gõ `!wcstop`."
                )
            else:
                await ctx.send(
                    f"ℹ️ Bạn đã vote rồi — hiện **{count}/{n_req}**; cần thêm **{left}** người."
                )
            return

        if self._db is None:
            await self._set_session(ch, active=False, stop_votes=set())
        else:
            await self._set_session(ch, active=False, stop_votes=[])
        board = await self._leaderboard_text(ctx.guild, ch)
        await ctx.send(
            f"🛑 Đủ **{n_req}** vote — đã dừng nối từ.\n\n{board}"
        )

    @commands.command(name="wchint")
    async def wchint(self, ctx: commands.Context):
        """Gợi ý một từ hợp lệ (theo file / danh sách gợi ý), chưa dùng trong phiên."""
        ch = ctx.channel.id
        sess = await self._session(ch)
        if not sess.get("active"):
            await ctx.send("ℹ️ Không có phiên đang chạy.")
            return
        last = sess.get("last_word")
        if not last:
            await ctx.send("💡 Gợi ý: hãy bắt đầu bằng một từ phổ biến (ví dụ `apple`, `water`).")
            return
        letter = last[-1]
        used = await self._gather_used(ch)
        candidates = self._hint_candidates(letter, used)
        if not candidates:
            await ctx.send(
                f"💭 Không tìm được gợi ý cho **`{letter}`** trong kho cục bộ. "
                "Thử tự nghĩ hoặc thêm `data/word_chain_words.txt`."
            )
            return
        pick = random.choice(candidates[:200])
        await ctx.send(
            f"💡 Gợi ý (chưa chơi trong phiên): **`{pick}`** — _chỉ là gợi ý, "
            f"vẫn phải pass từ điển khi kiểm tra bật._"
        )

    @commands.command(name="wchistory")
    async def wchistory(self, ctx: commands.Context):
        """Các từ đã chơi (gần nhất)."""
        ch = ctx.channel.id
        rows = await self._history(ch, limit=30)
        if not rows:
            await ctx.send("📜 Chưa có từ nào trong phiên (hoặc vừa `!wcstart` xong).")
            return
        lines = ["📜 **Lịch sử từ** (cũ → mới):", ""]
        for r in rows:
            who = self._display_name(ctx.guild, r["user_id"])
            lines.append(f"• **{r['word']}** — {who}")
        await ctx.send("\n".join(lines))

    @commands.command(name="wcscore")
    async def wcscore(self, ctx: commands.Context, member: discord.Member | None = None):
        """Xem điểm (bản thân hoặc @người khác)."""
        ch = ctx.channel.id
        target = member or ctx.author
        scores = await self._get_scores(ch)
        for doc in scores:
            if doc["user_id"] == target.id:
                await ctx.send(
                    f"⭐ {target.mention}: **{doc['score']}** điểm (phiên hiện tại trên kênh này)."
                )
                return
        await ctx.send(f"⭐ {target.mention}: **0** điểm.")

    @commands.command(name="wcleaderboard", aliases=["wclb"])
    async def wcleaderboard(self, ctx: commands.Context):
        """Bảng xếp hạng điểm phiên hiện tại (theo kênh)."""
        await ctx.send(await self._leaderboard_text(ctx.guild, ctx.channel.id))

    @commands.command(name="wcstatus")
    async def wcstatus(self, ctx: commands.Context):
        """Trạng thái: đang chơi hay không, từ cuối, chữ cần nối."""
        ch = ctx.channel.id
        sess = await self._session(ch)
        active = sess.get("active")
        last = sess.get("last_word")
        if not active:
            await ctx.send("ℹ️ Phiên **đang tắt**. Dùng `!wcstart` để chơi.")
            return
        if not last:
            await ctx.send(
                "🎯 Phiên **đang mở** — chưa có từ; gõ **một từ** (không `!`) trong kênh để bắt đầu."
            )
        else:
            await ctx.send(
                f"🎯 Đang chơi — từ hiện tại: **{last}** — cần từ bắt đầu bằng **`{last[-1]}`**."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WordChain(bot))
