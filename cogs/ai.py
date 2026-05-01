"""
Mod tự động: đọc lý do timeout từ audit log (lý do mod ghi khi áp timeout),
sau đó dùng Groq để đánh giá lời xin gỡ; nếu hợp lệ thì bot gỡ timeout.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager

import aiohttp
import discord
from discord.ext import commands

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_TEXT_FOR_API = 2000

# Chỉ gọi API khi tin có vẻ liên quan (tiết kiệm quota)
_KEYWORD_RE = re.compile(
    r"(timeout|time\s*out|mute|im\s*lặng|cấm\s*chat|không\s*nói|"
    r"mở\s*(timeout|mute)?|gỡ|bỏ\s*(timeout|mute)?|unmute|"
    r"xin\s*lỗi|nhầm|oan|appeal|thả|cho\s*nói)",
    re.IGNORECASE,
)

MIN_APPEAL_CONFIDENCE = 72
COOLDOWN_SEC = 86400

# Kênh lắng nghe tin tự nhiên (không dùng lệnh). Để rỗng [] = chỉ dùng !help (mọi kênh).
APPEAL_CHANNEL_IDS: list[int] = []

APPEAL_SYSTEM_PROMPT = """Bạn là bộ lọc yêu cầu gỡ communication timeout trên Discord (tiếng Việt và tiếng Anh).

Dữ liệu gồm hai phần theo thứ tự:
(1) LÝ DO BỊ HẠN CHẾ — lấy từ audit log khi timeout được áp (hoặc ghi chú nếu hệ thống không tra được).
(2) LỜI NHỜ / XIN GỠ — nội dung người nhờ gửi.

Nhiệm vụ: **Ưu tiên đánh giá mức độ / tính chất vi phạm từ (1)**, rồi xem (2) có đủ thuyết phục, thành khẩn và phù hợp để gỡ timeout không.
- true: lời xin hợp lý so với lý do mod; nhận lỗi / giải thích nhầm có căn cứ; lý do hạn chế nhẹ; đã chịu phạt đủ tương xứng; nhờ mở có thiện chí
- false: vi phạm nặng mà lời xin yếu hoặc không liên quan; trò đùa, spam; khiêu khích; xin gỡ nhưng thái độ không nghiêm túc so với (1)

Trả lời ĐÚNG định dạng JSON (không thêm gì khác):
{"is_appeal": <true hoặc false>, "confidence": <số_nguyên_0_đến_100>, "reason": "<một câu tiếng Việt>"}"""


def _channel_matches(message: discord.Message, watch_ids: list[int]) -> bool:
    if not watch_ids:
        return False
    watch_set = set(watch_ids)
    ch = message.channel
    if ch.id in watch_set:
        return True
    if isinstance(ch, discord.Thread) and ch.parent_id and ch.parent_id in watch_set:
        return True
    return False


def _prefilter_text(message: discord.Message) -> bool:
    t = (message.content or "").strip()
    if not t:
        return False
    me = message.guild.me if message.guild else None
    if me and me in message.mentions:
        return True
    if _KEYWORD_RE.search(t):
        return True
    return False


def _audit_diff_communication_disabled(diff: object | None) -> object | None:
    """Giá trị timeout trên AuditLogDiff.after (tương thích nhiều phiên discord.py)."""
    if diff is None:
        return None
    for attr in ("timed_out_until", "communication_disabled_until"):
        val = getattr(diff, attr, None)
        if val is not None:
            return val
    return None


@asynccontextmanager
async def _typing_if_supported(channel: discord.abc.Messageable):
    try:
        async with channel.typing():
            yield
    except (AttributeError, discord.ClientException, TypeError, NotImplementedError):
        yield


async def _resolve_target_member(message: discord.Message) -> discord.Member | None:
    guild = message.guild
    if not guild:
        return None

    members = [
        m
        for m in message.mentions
        if not m.bot and isinstance(m, discord.Member)
    ]
    for m in members:
        if m.is_timed_out():
            return m
    if len(members) == 1:
        return members[0]
    if len(members) > 1:
        return members[0]

    ref = message.reference
    if ref and ref.message_id:
        ref_msg = ref.resolved
        if ref_msg is None:
            try:
                ref_msg = await message.channel.fetch_message(ref.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                ref_msg = None
        if isinstance(ref_msg, discord.Message) and ref_msg.author and not ref_msg.author.bot:
            m = guild.get_member(ref_msg.author.id)
            if m:
                return m

    author = message.author
    if isinstance(author, discord.Member) and author.is_timed_out():
        return author

    return None


class AIModTimeoutAppeal(commands.Cog):
    """Đọc lý do timeout từ audit log, rồi dùng Groq đánh giá xin gỡ; cần Moderate Members + View Audit Log."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldown: dict[tuple[int, int], float] = {}

    def _cooldown_ok(self, guild_id: int, author_id: int) -> bool:
        key = (guild_id, author_id)
        now = time.monotonic()
        last = self._cooldown.get(key, 0.0)
        if now - last < COOLDOWN_SEC:
            return False
        self._cooldown[key] = now
        return True

    def _compose_command_appeal_text(
        self,
        requester: discord.Member,
        target: discord.Member,
        reason: str,
    ) -> str:
        r = (reason or "").strip() or "(không ghi lý do cụ thể)"
        return (
            f"Người nhờ bot: {requester.display_name} (ID {requester.id}).\n"
            f"Muốn gỡ timeout cho: {target.display_name} (nick: {target.name}).\n"
            f"Lời xin / giải thích thêm: {r}"
        )

    async def _fetch_audit_timeout_reason(self, guild: discord.Guild, member_id: int) -> tuple[bool, str]:
        """(ok, text). ok=False → text là lỗi gửi user và dừng luồng. ok=True → text là lý do mod (hoặc placeholder) đưa cho AI."""
        me = guild.me
        if not me or not me.guild_permissions.view_audit_log:
            return (
                False,
                "Bot cần quyền **View Audit Log** để đọc lý do timeout (ghi khi mod áp timeout) trước khi AI xét duyệt.",
            )

        try:
            async for entry in guild.audit_logs(limit=200, action=discord.AuditLogAction.member_update):
                target = getattr(entry, "target", None)
                if target is None or getattr(target, "id", None) != member_id:
                    continue
                after_val = _audit_diff_communication_disabled(getattr(entry, "after", None))
                if after_val is None:
                    continue
                raw = (entry.reason or "").strip()
                line = raw if raw else "(mod không ghi lý do trong audit log)"
                logger.info("[AI-MOD] Đã tra audit timeout user=%s reason=%r", member_id, line[:120])
                return (True, line)
        except discord.Forbidden:
            return (False, "Bot không đọc được audit log — kiểm tra quyền **View Audit Log**.")
        except Exception:
            logger.exception("[AI-MOD] Lỗi khi đọc audit log user=%s", member_id)
            return (False, "Lỗi khi đọc audit log — thử lại sau.")

        placeholder = (
            "(Không tìm thấy entry áp timeout gần đây trong audit log — có thể đã quá giới hạn Discord lưu, "
            "hoặc timeout không xuất hiện trong audit của server này.)"
        )
        logger.warning("[AI-MOD] Không match audit timeout cho user=%s", member_id)
        return (True, placeholder)

    def _wrap_appeal_with_restriction(self, restriction_reason: str, appeal_text: str) -> str:
        return (
            "[LÝ DO BỊ HẠN CHẾ — theo audit log khi áp communication timeout gần nhất]\n"
            f"{restriction_reason}\n\n"
            "[LỜI NHỜ / XIN GỠ]\n"
            f"{appeal_text.strip()}"
        )

    async def _analyze_appeal(self, text: str) -> dict | None:
        api_key = config.GROQ_API_KEY
        if not api_key:
            logger.error("[AI-MOD] GROQ_API_KEY chưa cấu hình")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        safe = text[:MAX_TEXT_FOR_API]
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": APPEAL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Phân tích tin nhắn sau:\n\n{json.dumps(safe)}",
                },
            ],
            "temperature": 0.15,
            "max_tokens": 180,
        }
        content = ""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        logger.error("[AI-MOD] Groq HTTP %s: %s", resp.status, await resp.text())
                        return None
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("[AI-MOD] JSON Groq lỗi: %s | raw=%r", e, content)
            return None
        except Exception as e:
            logger.error("[AI-MOD] Lỗi Groq: %s", e)
            return None

    async def _reply(self, message: discord.Message | None, ctx: commands.Context | None, content: str, **kwargs):
        if ctx is not None:
            await ctx.send(content, **kwargs)
        elif message is not None:
            await message.reply(content, mention_author=False, **kwargs)

    async def _run_appeal_flow(
        self,
        *,
        requester: discord.Member,
        target: discord.Member,
        text_for_ai: str,
        ctx: commands.Context | None = None,
        message: discord.Message | None = None,
    ):
        """Chung cho !help và on_message: AI quyết định → gỡ timeout hoặc báo từ chối."""
        guild = requester.guild
        if guild is None:
            return

        me = guild.me
        if not me or not me.guild_permissions.moderate_members:
            await self._reply(
                message,
                ctx,
                "Bot cần quyền **Moderate Members** trên server.",
            )
            return

        if target.bot:
            await self._reply(message, ctx, "Không áp dụng cho bot.")
            return

        if not target.is_timed_out():
            await self._reply(
                message,
                ctx,
                f"{target.mention} hiện **không** đang bị timeout.",
            )
            return

        ok_audit, restriction_or_err = await self._fetch_audit_timeout_reason(guild, target.id)
        if not ok_audit:
            await self._reply(message, ctx, restriction_or_err)
            return

        if not self._cooldown_ok(guild.id, requester.id):
            await self._reply(message, ctx, f"{requester.mention} chờ **{int(COOLDOWN_SEC)}s** rồi thử lại.")
            return

        payload = self._wrap_appeal_with_restriction(restriction_or_err, text_for_ai)

        ch = ctx.channel if ctx else (message.channel if message else None)
        if ch:
            async with _typing_if_supported(ch):
                result = await self._analyze_appeal(payload)
        else:
            result = await self._analyze_appeal(payload)

        if not result:
            await self._reply(
                message,
                ctx,
                "Không gọi được AI — kiểm tra `GROQ_API_KEY` hoặc thử lại sau.",
            )
            return

        try:
            is_appeal = bool(result.get("is_appeal"))
            conf = int(result.get("confidence", 0))
            ai_reason = str(result.get("reason", "")).strip()
        except (TypeError, ValueError):
            is_appeal, conf, ai_reason = False, 0, ""

        if not is_appeal or conf < MIN_APPEAL_CONFIDENCE:
            extra = f"\n*({ai_reason})*" if ai_reason else ""
            await self._reply(
                message,
                ctx,
                f"❌ Rambo **chưa đồng ý** gỡ timeout cho {target.mention} "
                f"(điểm tin cậy lời nhờ: **{conf}%**, cần ≥ **{MIN_APPEAL_CONFIDENCE}%**).{extra}",
            )
            logger.info(
                "[AI-MOD] Từ chối gỡ timeout %s — appeal=%s conf=%s",
                target.id,
                is_appeal,
                conf,
            )
            return

        try:
            await target.timeout(
                None,
                reason=f"AI-mod !help: từ {requester} (conf {conf}%)",
            )
        except discord.Forbidden:
            logger.warning("[AI-MOD] Forbidden khi gỡ timeout %s", target.id)
            await self._reply(
                message,
                ctx,
                "Bot không đủ quyền hoặc không gỡ được người này (thứ bậc role / quyền).",
            )
            return
        except discord.HTTPException as e:
            logger.error("[AI-MOD] HTTP khi timeout(None): %s", e)
            await self._reply(message, ctx, f"Lỗi khi gỡ timeout: `{e}`")
            return

        logger.info("[AI-MOD] Đã gỡ timeout cho %s (conf=%s)", target.id, conf)
        await self._reply(
            message,
            ctx,
            f"✅ Đã gỡ timeout cho {target.mention}. (AI: **{conf}%**)",
        )

    @commands.command(
        name="help",
        aliases=["helptimeout", "gỡtimeout", "gotimeout"],
        help="!help @member <lý do> — bot đọc lý do timeout (audit) rồi AI xét lời xin; cần View Audit Log.",
    )
    async def cmd_help_timeout(self, ctx: commands.Context, member: discord.Member, *, reason: str = ""):
        """!help @member — nhờ AI xem có mở timeout không (kèm lý do sau mention)."""
        if ctx.guild is None:
            await ctx.send("Lệnh chỉ dùng trong server.")
            return

        text_for_ai = self._compose_command_appeal_text(ctx.author, member, reason)
        await self._run_appeal_flow(
            requester=ctx.author,
            target=member,
            text_for_ai=text_for_ai,
            ctx=ctx,
            message=None,
        )

    @cmd_help_timeout.error
    async def cmd_help_timeout_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MemberNotFound):
            await ctx.send("Không tìm thấy member — hãy **mention** hoặc dùng ID hợp lệ.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "**Cách dùng:** `!help @member <lý do>`\n"
                "Ví dụ: `!help @user em xin lỗi, nhờ bot xem xét gỡ timeout`"
            )
            return
        raise error

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        watch = APPEAL_CHANNEL_IDS
        if not watch or not _channel_matches(message, watch):
            return

        if not message.guild:
            return

        if not _prefilter_text(message):
            return

        if not isinstance(message.author, discord.Member):
            return

        me = message.guild.me
        if not me or not me.guild_permissions.moderate_members:
            return

        target = await _resolve_target_member(message)
        if target is None:
            return

        text = (message.content or "").strip()
        if not text:
            return

        await self._run_appeal_flow(
            requester=message.author,
            target=target,
            text_for_ai=text,
            ctx=None,
            message=message,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AIModTimeoutAppeal(bot))
