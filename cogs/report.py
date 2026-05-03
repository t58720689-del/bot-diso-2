"""
Báo cáo tin vi phạm: người dùng **reply** tin cần báo và **tag @bot** → Groq AI xét
chửi thề/tục và link mời Discord gợi nội dung 18+/NSFW. Mỗi người 7200s / lần.
Nếu AI xác định vi phạm đủ độ tin cậy → bot xóa tin bị reply (cần Manage Messages).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
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

# Mỗi member / server: tối thiểu 7200 giây giữa hai lần báo cáo
REPORT_COOLDOWN_SEC = 3600
# Chỉ xóa tin khi AI khẳng định vi phạm với độ tin cậy >= ngưỡng này
MIN_VIOLATION_CONFIDENCE = 75

# Kênh nhận log kết quả (embed). Để [] = không gửi log ra kênh.
REPORT_LOG_CHANNEL_IDS: list[int] = []

_HINT_REPLY_TAG = (
    "Để **báo cáo vi phạm**: hãy **Trả lời (Reply)** vào tin cần báo, "
    "rồi trong ô reply gõ tin nhắn có tag bot: {bot_mention}"
)

REPORT_SYSTEM_PROMPT = """Bạn là bộ lọc vi phạm nội dung chat Discord (tiếng Việt và tiếng Anh).

Một tin được coi là VI PHẠM nếu có ít nhất một trong các dấu hiệu sau (kể cả teencode, không dấu, viết tắt):
1) Chửi thề, chửi tục, lời lẽ thô tục hướng tới người khác, xúc phạm nặng, quấy rối bằng ngôn từ tục.
2) Link mời Discord (discord.gg, discord.com/invite, dis.gd, …) hoặc lời mời tham gia server/kênh có dấu hiệu quảng bá nội dung người lớn, 18+, NSFW, "gái gú", khiêu dâm, onlyfans, dating lạ, nhóm chat "hot", v.v.

KHÔNG coi là vi phạm nếu:
- Chỉ trò chuyện bình thường, không có tục ngữ xúc phạm nặng.
- Link mời server học tập, game, cộng đồng chung không gợi dục.
- Chỉ nhắc từ nhạy cảm trong ngữ cảnh báo chính sách / thảo luận trung lập, không quảng bá.

Trả lời ĐÚNG một đối tượng JSON (không thêm chữ ngoài JSON):
{"violates": <true hoặc false>, "confidence": <số_nguyên_0_đến_100>, "reason": "<một câu tiếng Việt ngắn>"}"""


def _message_text_for_ai(message: discord.Message) -> str:
    parts: list[str] = []
    if message.content and message.content.strip():
        parts.append(message.content.strip())
    for em in message.embeds:
        if em.title:
            parts.append(em.title)
        if em.description:
            parts.append(em.description)
        for f in em.fields:
            if f.name:
                parts.append(f.name)
            if f.value:
                parts.append(f.value)
        if em.url:
            parts.append(em.url)
    for a in message.attachments:
        parts.append(a.url)
    return "\n".join(parts).strip()


@asynccontextmanager
async def _typing_if_supported(channel: discord.abc.Messageable):
    try:
        async with channel.typing():
            yield
    except (AttributeError, discord.ClientException, TypeError, NotImplementedError):
        yield


class MessageViolationReport(commands.Cog):
    """Reply tin vi phạm + tag bot → AI (Groq) → xóa tin nếu vi phạm."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldown: dict[tuple[int, int], float] = {}

    def _cooldown_remaining(self, guild_id: int, reporter_id: int) -> float:
        key = (guild_id, reporter_id)
        last = self._cooldown.get(key, 0.0)
        elapsed = time.monotonic() - last
        return max(0.0, REPORT_COOLDOWN_SEC - elapsed)

    def _note_cooldown(self, guild_id: int, reporter_id: int) -> None:
        self._cooldown[(guild_id, reporter_id)] = time.monotonic()

    async def _analyze(self, text: str) -> dict | None:
        api_key = getattr(config, "GROQ_API_KEY", "") or ""
        if not api_key:
            logger.error("[REPORT] GROQ_API_KEY chưa cấu hình")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        safe = text[:MAX_TEXT_FOR_API]
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Nội dung tin nhắn cần xét (một chuỗi JSON đã escape):\n{json.dumps(safe)}",
                },
            ],
            "temperature": 0.12,
            "max_tokens": 220,
        }
        content = ""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        logger.error("[REPORT] Groq HTTP %s: %s", resp.status, await resp.text())
                        return None
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("[REPORT] JSON Groq lỗi: %s | raw=%r", e, content)
            return None
        except Exception as e:
            logger.error("[REPORT] Lỗi Groq: %s", e)
            return None

    async def _send_log(
        self,
        *,
        guild: discord.Guild,
        reporter: discord.abc.User,
        target: discord.abc.User,
        message: discord.Message,
        violates: bool,
        confidence: int,
        reason: str,
        deleted: bool,
    ) -> None:
        if not REPORT_LOG_CHANNEL_IDS:
            return
        emb = discord.Embed(
            title="Báo cáo vi phạm",
            color=discord.Color.red() if violates else discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        emb.add_field(name="Server", value=guild.name, inline=True)
        emb.add_field(name="Kênh", value=f"<#{message.channel.id}>", inline=True)
        emb.add_field(name="Người báo cáo", value=f"{reporter} ({reporter.id})", inline=False)
        emb.add_field(name="Tác giả tin", value=f"{target} ({target.id})", inline=False)
        emb.add_field(name="Kết quả AI", value=f"violates={violates}, confidence={confidence}%", inline=True)
        emb.add_field(name="Đã xóa tin", value="Có" if deleted else "Không", inline=True)
        if reason:
            emb.add_field(name="Lý do (AI)", value=reason[:1024], inline=False)
        link = message.jump_url
        emb.add_field(name="Jump", value=link, inline=False)
        for ch_id in REPORT_LOG_CHANNEL_IDS:
            ch = self.bot.get_channel(ch_id)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(ch_id)
                except discord.HTTPException:
                    continue
            if isinstance(ch, discord.abc.Messageable):
                try:
                    await ch.send(embed=emb)
                except discord.HTTPException as e:
                    logger.warning("[REPORT] Không gửi log kênh %s: %s", ch_id, e)

    async def _execute_report(
        self,
        *,
        guild: discord.Guild,
        reporter: discord.Member,
        target_message: discord.Message,
        respond: Callable[[str], Awaitable[None]],
        typing_channel: discord.abc.Messageable,
    ) -> None:
        if target_message.guild is None or target_message.guild.id != guild.id:
            await respond("Tin báo cáo không thuộc server này.")
            return

        if reporter.id == target_message.author.id:
            await respond("Không thể báo cáo chính tin của mình.")
            return

        if target_message.author.bot:
            await respond("Không báo cáo tin từ bot theo luồng này.")
            return

        remain = self._cooldown_remaining(guild.id, reporter.id)
        if remain > 0:
            await respond(
                f"Bạn vừa báo cáo gần đây. Đợi **{int(remain)}** giây nữa "
                f"(tối đa **{REPORT_COOLDOWN_SEC}** giây giữa hai lần)."
            )
            return

        text = _message_text_for_ai(target_message)
        if not text:
            await respond("Tin không có nội dung chữ/embed để AI xét.")
            return

        me = guild.me
        if not me or not me.guild_permissions.manage_messages:
            await respond(
                "Bot cần quyền **Manage Messages** trên server để có thể xóa tin vi phạm."
            )
            return

        async with _typing_if_supported(typing_channel):
            result = await self._analyze(text)

        if not result:
            await respond(
                "Không gọi được AI (kiểm tra `GROQ_API_KEY` hoặc thử lại sau). **Cooldown chưa bị trừ.**"
            )
            return

        try:
            violates = bool(result.get("violates"))
            confidence = int(result.get("confidence", 0))
            reason = str(result.get("reason", "")).strip()
        except (TypeError, ValueError):
            violates, confidence, reason = False, 0, ""

        self._note_cooldown(guild.id, reporter.id)

        deleted = False
        if violates and confidence >= MIN_VIOLATION_CONFIDENCE:
            try:
                await target_message.delete()
                deleted = True
            except discord.NotFound:
                await respond(
                    f"Tin đã bị xóa trước đó. (AI: vi phạm **{confidence}%** — {reason})"
                )
                await self._send_log(
                    guild=guild,
                    reporter=reporter,
                    target=target_message.author,
                    message=target_message,
                    violates=True,
                    confidence=confidence,
                    reason=reason,
                    deleted=False,
                )
                return
            except discord.Forbidden:
                await respond(
                    f"AI xác định vi phạm (**{confidence}%**) nhưng bot **không xóa được** tin "
                    "(thiếu quyền hoặc thứ bậc role)."
                )
                await self._send_log(
                    guild=guild,
                    reporter=reporter,
                    target=target_message.author,
                    message=target_message,
                    violates=True,
                    confidence=confidence,
                    reason=reason,
                    deleted=False,
                )
                return

            logger.info(
                "[REPORT] Đã xóa tin msg=%s author=%s reporter=%s conf=%s",
                target_message.id,
                target_message.author.id,
                reporter.id,
                confidence,
            )
            await respond(
                f"Đã **xóa** tin vi phạm (AI **{confidence}%**). Cảm ơn bạn đã báo cáo.\n*{reason}*"
            )
        else:
            await respond(
                f"AI **không** xác định đủ mức vi phạm để xóa "
                f"(violates={violates}, **{confidence}%**, cần ≥ **{MIN_VIOLATION_CONFIDENCE}%**).\n"
                f"*{reason or '—'}*"
            )

        await self._send_log(
            guild=guild,
            reporter=reporter,
            target=target_message.author,
            message=target_message,
            violates=violates,
            confidence=confidence,
            reason=reason,
            deleted=deleted,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        me = message.guild.me
        if me is None or me not in message.mentions:
            return

        if not isinstance(message.author, discord.Member):
            return

        ref = message.reference
        if ref is None or ref.message_id is None:
            hint = _HINT_REPLY_TAG.format(bot_mention=me.mention)
            try:
                await message.reply(hint, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
            return

        target: discord.Message | None = None
        if isinstance(ref.resolved, discord.Message):
            target = ref.resolved
        else:
            try:
                target = await message.channel.fetch_message(ref.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                target = None

        if target is None:
            try:
                await message.reply(
                    "Không lấy được tin bạn reply — thử lại hoặc kiểm tra quyền đọc tin.",
                    mention_author=False,
                )
            except discord.HTTPException:
                pass
            return

        async def respond(text: str) -> None:
            await message.reply(
                text,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        await self._execute_report(
            guild=message.guild,
            reporter=message.author,
            target_message=target,
            respond=respond,
            typing_channel=message.channel,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageViolationReport(bot))
    logger.info("[REPORT] MessageViolationReport cog loaded")
