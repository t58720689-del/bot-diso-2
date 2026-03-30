import json
import random
import re
from pathlib import Path

import discord
from discord.ext import commands

from utils.logger import setup_logger

logger = setup_logger(__name__)
#channel id game2
_WORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "words.txt"
_CHANNEL_ID = 1488192144438071433
_NON_WORD = re.compile(r"[0-9@#$%^&*()+={}\[\]|\\<>/~`\"_]")
_SKIP_MIN_PLAYERS = 5
_ADMIN_ROLE_ID = 1185158470958333953


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


class _Session:
    __slots__ = ("active", "last_word", "used", "scores", "players", "skip_votes")

    def __init__(self):
        self.active: bool = False
        self.last_word: str | None = None
        self.used: set[str] = set()
        self.scores: dict[int, int] = {}
        self.players: set[int] = set()
        self.skip_votes: set[int] = set()


class Game2(commands.Cog):
    """Nối từ tiếng Việt — từ tiếp phải bắt đầu bằng âm tiết cuối của từ trước."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sessions: dict[int, _Session] = {}
        self._lexicon: set[str] = set()

    async def cog_load(self) -> None:
        self._lexicon = _load_lexicon()

    def _sess(self, ch: int) -> _Session:
        if ch not in self._sessions:
            self._sessions[ch] = _Session()
        return self._sessions[ch]

    def _in_lexicon(self, word: str) -> bool:
        return word in self._lexicon

    @staticmethod
    def _name(guild: discord.Guild | None, uid: int) -> str:
        if guild:
            m = guild.get_member(uid)
            if m:
                return m.display_name
        return f"User {uid}"

    def _leaderboard(
        self,
        guild: discord.Guild | None,
        s: _Session,
        *,
        limit: int = 15,
        title: str = "🏆 **Bảng xếp hạng** (phiên hiện tại)",
    ) -> str:
        if not s.scores:
            return f"{title}\n_Chưa có lượt hợp lệ nào._"
        ranked = sorted(
            s.scores.items(),
            key=lambda x: (-x[1], self._name(guild, x[0]).lower()),
        )
        lines = [title, ""]
        for i, (uid, pts) in enumerate(ranked[:limit], start=1):
            lines.append(f"**{i}.** {self._name(guild, uid)} — **{pts}** từ")
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
        s: _Session,
        guild: discord.Guild | None,
    ) -> None:
        if s.last_word is None:
            return

        tail = _last_syl(s.last_word)
        has_next = any(
            w for w in self._lexicon
            if w not in s.used and len(w.split()) >= 2 and _first_syl(w) == tail
        )
        if has_next:
            return

        parts: list[str] = [f"🚫 Không còn từ nào bắt đầu bằng **`{tail}`** — hết từ nối!"]

        if s.scores:
            winner_id = max(s.scores, key=lambda k: s.scores[k])
            winner_name = self._name(guild, winner_id)
            winner_score = s.scores[winner_id]
            parts.append(
                f"\n🎉 **Người chiến thắng: {winner_name}** với **{winner_score}** từ!"
            )
            parts.append(
                self._leaderboard(guild, s, title="\n🏆 **Bảng xếp hạng**")
            )

        candidates = [
            w for w in self._lexicon if w not in s.used and len(w.split()) >= 2
        ]
        if candidates:
            new_word = random.choice(candidates)
            s.used.add(new_word)
            s.last_word = new_word
            s.skip_votes.clear()
            new_tail = _last_syl(new_word)
            parts.append(
                f"\n🔄 Bot chọn từ mới: **{new_word}** — từ tiếp theo phải bắt đầu bằng **`{new_tail}`**.\n"
                f"Đã dùng **{len(s.used)}** từ."
            )
        else:
            parts.append("\n⚠️ Từ điển đã hết từ! Dùng `!ntvstart` để bắt đầu phiên mới.")
            s.active = False

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

        s = self._sess(message.channel.id)
        if not s.active:
            return

        s.players.add(message.author.id)

        if s.last_word is None:
            if not self._in_lexicon(word):
                try:
                    await message.reply(
                        f"❌ **{content}** không có trong từ điển. Thử từ khác.",
                        delete_after=15,
                    )
                except discord.HTTPException:
                    pass
                return
            s.last_word = word
            s.used.add(word)
            s.scores[message.author.id] = s.scores.get(message.author.id, 0) + 1
            s.skip_votes.clear()
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
            await self._check_dead_end(message.channel, s, message.guild)
            return

        need = _last_syl(s.last_word)
        got = _first_syl(word)
        if got != need:
            try:
                await message.reply(
                    f"❌ Từ phải bắt đầu bằng **`{need}`** (theo từ trước: **{s.last_word}**).",
                    delete_after=12,
                )
            except discord.HTTPException:
                pass
            return

        if word in s.used:
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

        s.used.add(word)
        s.last_word = word
        s.scores[message.author.id] = s.scores.get(message.author.id, 0) + 1
        s.skip_votes.clear()
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass
        await self._check_dead_end(message.channel, s, message.guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.is_timed_out() or not after.is_timed_out():
            return
        for ch_id, s in self._sessions.items():
            if not s.active or after.id not in s.players:
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
        s = self._sess(ctx.channel.id)
        s.used.clear()
        s.scores.clear()
        s.last_word = None
        s.active = True
        s.players.clear()
        s.skip_votes.clear()
        await ctx.send(embed=self._guide_embed())

        if word:
            n = _normalize(word)
            if not n or len(n.split()) < 2:
                s.active = False
                await ctx.send(
                    "❌ Từ khởi đầu phải có **ít nhất 2 âm tiết** (VD: *xin chào*).",
                    delete_after=15,
                )
                return
            if not self._in_lexicon(n):
                s.active = False
                await ctx.send(
                    f"❌ **{word}** không có trong từ điển. Thử từ khác.",
                    delete_after=18,
                )
                return
            s.last_word = n
            s.used.add(n)
            s.scores[ctx.author.id] = s.scores.get(ctx.author.id, 0) + 1
            s.players.add(ctx.author.id)
            tail = _last_syl(n)
            await ctx.send(
                f"🔤 Phiên mới! Từ đầu: **{n}** — từ tiếp theo phải bắt đầu bằng **`{tail}`**.\n"
                f"Không được lặp từ đã dùng ({len(s.used)} từ). Xem điểm: `!ntvleaderboard`.",
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
        """Kết thúc phiên nối từ tiếng Việt hiện tại."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        s = self._sess(ctx.channel.id)
        board = None
        if s.active and s.scores:
            board = self._leaderboard(
                ctx.guild, s, title="🏆 **Kết thúc phiên** — bảng xếp hạng",
            )
        s.active = False
        s.last_word = None
        s.used.clear()
        s.scores.clear()
        s.players.clear()
        s.skip_votes.clear()
        msg = "⏹️ Đã kết thúc phiên nối từ tiếng Việt."
        if board:
            msg += "\n\n" + board
        await ctx.send(msg, delete_after=45 if board else 12)

    @commands.command(name="ntvstatus")
    async def ntvstatus(self, ctx: commands.Context):
        """Xem trạng thái phiên nối từ tiếng Việt."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        s = self._sess(ctx.channel.id)
        if not s.active:
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=12,
            )
            return
        if s.last_word is None:
            await ctx.send(
                f"Đang chờ **từ đầu tiên**. Đã dùng: {len(s.used)} từ.", delete_after=12,
            )
            return
        tail = _last_syl(s.last_word)
        await ctx.send(
            f"Từ hiện tại: **{s.last_word}** — từ tiếp theo bắt đầu bằng **`{tail}`**.\n"
            f"Đã dùng **{len(s.used)}** từ (không được lặp). "
            "Bảng điểm: `!ntvleaderboard`.",
            delete_after=20,
        )

    @commands.command(name="ntvskip")
    async def ntvskip(self, ctx: commands.Context):
        """Vote bỏ qua từ hiện tại — cần ≥ 5 người dùng lệnh này."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        s = self._sess(ctx.channel.id)
        if not s.active:
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=12,
            )
            return
        if s.last_word is None:
            await ctx.send("⚠️ Chưa có từ nào để skip. Hãy gửi từ đầu tiên.", delete_after=12)
            return

        if ctx.author.id in s.skip_votes:
            await ctx.send("⚠️ Bạn đã vote skip rồi. Chờ người khác vote thêm.", delete_after=10)
            return

        s.skip_votes.add(ctx.author.id)
        current = len(s.skip_votes)
        needed = _SKIP_MIN_PLAYERS

        if current < needed:
            await ctx.send(
                f"🗳️ **{ctx.author.display_name}** vote skip! "
                f"(**{current}/{needed}** — cần thêm **{needed - current}** vote nữa)",
                delete_after=20,
            )
            return

        candidates = [w for w in self._lexicon if w not in s.used and len(w.split()) >= 2]
        if not candidates:
            await ctx.send("⚠️ Không còn từ nào trong từ điển để chọn!", delete_after=15)
            return
        new_word = random.choice(candidates)
        s.used.add(new_word)
        s.last_word = new_word
        s.skip_votes.clear()
        tail = _last_syl(new_word)
        await ctx.send(
            f"⏭️ Đủ **{needed}** vote — đã skip! Từ mới: **{new_word}** — "
            f"từ tiếp theo phải bắt đầu bằng **`{tail}`**.\n"
            f"Đã dùng **{len(s.used)}** từ.",
        )

    @commands.command(name="ntvleaderboard", aliases=["ntvrank", "ntvtop"])
    async def ntvleaderboard(self, ctx: commands.Context):
        """Bảng xếp hạng phiên nối từ tiếng Việt hiện tại."""
        if ctx.channel.id != _CHANNEL_ID:
            return
        s = self._sess(ctx.channel.id)
        if not s.active:
            await ctx.send(
                "Chưa có phiên đang chơi. Dùng `!ntvstart` để bắt đầu.", delete_after=15,
            )
            return
        await ctx.send(self._leaderboard(ctx.guild, s), delete_after=60)

    @commands.command(name="ntvadd")
    async def ntvadd(self, ctx: commands.Context, *, word: str = None):
        """Thêm từ mới vào từ điển. Chỉ dành cho người có role đặc biệt."""
        if not any(r.id == _ADMIN_ROLE_ID for r in ctx.author.roles):
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

        self._lexicon.add(n)
        try:
            with open(_WORDS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"text": n}, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("[GAME2] Failed to write word to %s: %s", _WORDS_PATH, e)
            await ctx.send(f"❌ Lỗi khi ghi file: {e}", delete_after=15)
            return

        await ctx.send(
            f"✅ Đã thêm **{n}** vào từ điển. Tổng: **{len(self._lexicon):,}** từ."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Game2(bot))
