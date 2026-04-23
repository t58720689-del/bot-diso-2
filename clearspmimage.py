"""
Quét ảnh trong kênh chỉ định bằng Groq (vision): nội dung khiêu dâm hoặc lừa đảo (scam crypto / casino giả…).

Kênh: CLEARSPM_IMAGE_CHANNEL_IDS (config.py). Tùy CLEARSPM_IMAGE_REQUIRE_MENTION: cần @bot hoặc quét mọi
tin có ảnh. Chỉ phân tích ảnh đính kèm (tối đa 3/tin), không gửi nội dung text cho AI.
"""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import aiohttp
import discord
from discord.ext import commands

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Fallback nếu config chưa khai báo danh sách
_DEFAULT_WATCH_IDS: set[int] = {1411520340508807178}


def _watch_channel_ids() -> set[int]:
    raw = getattr(config, "CLEARSPM_IMAGE_CHANNEL_IDS", None) or []
    if isinstance(raw, (list, tuple, set)) and len(raw) > 0:
        return {int(x) for x in raw}
    return set(_DEFAULT_WATCH_IDS)


def _require_mention() -> bool:
    return bool(getattr(config, "CLEARSPM_IMAGE_REQUIRE_MENTION", True))

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Model đa phương tiện (ảnh) — llama-3.2-90b-vision-preview đã bị Groq gỡ; xem console.groq.com/docs/vision
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

MAX_IMAGES_PER_MESSAGE = 3
MAX_IMAGE_BYTES = 3_800_000
TIMEOUT_DURATION = timedelta(hours=180)

_GROQ_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15, sock_read=50)

_MESSAGEABLE_GUILD = (discord.TextChannel, discord.Thread, discord.VoiceChannel)


@asynccontextmanager
async def _typing_if_supported(channel: discord.abc.Messageable):
    """Bật typing khi gọi AI; kênh không hỗ trợ thì bỏ qua, không làm hỏng luồng."""
    try:
        async with channel.typing():
            yield
    except (AttributeError, discord.ClientException, TypeError, NotImplementedError):
        yield


# Discord đôi khi không gửi content_type; bổ sung theo phần mở rộng tên file
_IMAGE_EXTS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

SYSTEM_PROMPT = """You are a safety filter for images on Discord. You may ONLY rely on the visual content of the attached images (no user text descriptions).

Your task: detect
1) Clearly sexual/explicit content (sensitive nudity, sexual acts, pornography).
2) Scams conveyed through imagery (for example: fake crypto casino ads, fake crypto giveaways, celebrity impersonation pushing sign-ups, fake "Withdrawal Success" screens, fake USDT receipts, promo codes plus suspicious deposit/withdraw dashboards).

Do NOT treat as violations: harmless memes, games, cat photos, ordinary chat, legitimate app UIs that do not bait deposits or giveaways.

Reply with EXACTLY one JSON object (no markdown, no extra text):
{"violation": <true or false>, "category": "<one of: sex | scam | clean>", "reason": "<brief explanation in English>"}

- violation=true only when you are sure there is clearly sexual content OR a clear scam through the image.
- category must be "sex", "scam", or "clean"."""


def _channel_matches_watch(message: discord.Message, watch_ids: set[int]) -> bool:
    if not watch_ids:
        return False
    ch = message.channel
    if ch.id in watch_ids:
        return True
    if isinstance(ch, discord.Thread) and ch.parent_id and ch.parent_id in watch_ids:
        return True
    return False


def _should_bypass(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_messages


def _truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "có")
    return bool(val)


def _attachment_image_mime(att: discord.Attachment) -> str | None:
    ct = (att.content_type or "").lower()
    if ct.startswith("image/"):
        return ct if "/" in ct else "image/jpeg"
    if ct in ("", "application/octet-stream", "binary/octet-stream"):
        name = (att.filename or "").lower()
        for ext, mime in _IMAGE_EXTS.items():
            if name.endswith(ext):
                return mime
    return None


def _collect_candidate_attachments(message: discord.Message) -> list[discord.Attachment]:
    """Gom attachment từ tin gốc + forward (message_snapshots) + reply (reference.resolved)."""
    seen: set[int] = set()
    out: list[discord.Attachment] = []

    def _add(att_list):
        for a in att_list or []:
            if isinstance(a, discord.Attachment) and a.id not in seen:
                seen.add(a.id)
                out.append(a)

    _add(message.attachments)

    # Forwarded messages (Discord Message Forwarding) — discord.py ≥ 2.4
    snaps = getattr(message, "message_snapshots", None) or []
    for snap in snaps:
        _add(getattr(snap, "attachments", None))

    # Reply có ảnh trong tin được trả lời
    ref = getattr(message, "reference", None)
    if ref is not None:
        resolved = getattr(ref, "resolved", None)
        if isinstance(resolved, discord.Message):
            _add(resolved.attachments)

    return out


async def _read_image_attachments(message: discord.Message) -> list[dict]:
    """Trả về danh sách block image_url cho Groq (tối đa 3 ảnh), gồm cả forward & reply."""
    blocks: list[dict] = []
    for att in _collect_candidate_attachments(message):
        if len(blocks) >= MAX_IMAGES_PER_MESSAGE:
            break
        mime = _attachment_image_mime(att)
        if not mime:
            continue
        try:
            data = await asyncio.wait_for(att.read(), timeout=45.0)
        except (asyncio.TimeoutError, discord.HTTPException, OSError) as e:
            logger.warning("[CLEARSPMIMAGE] Không đọc được attachment %s: %s", att.filename, e)
            continue
        if len(data) > MAX_IMAGE_BYTES:
            logger.warning(
                "[CLEARSPMIMAGE] Bỏ qua ảnh quá lớn (%s bytes): %s",
                len(data),
                att.filename,
            )
            continue
        b64 = base64.standard_b64encode(data).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return blocks


class ClearSpmImage(commands.Cog):
    """Groq vision: CLEARSPM_IMAGE_CHANNEL_IDS — ảnh sex/scam → xóa tin, timeout, thông báo (tùy REQUIRE_MENTION)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _groq_classify_images(self, image_blocks: list[dict]) -> dict | None:
        api_key = config.GROQ_API_KEY
        if not api_key:
            logger.error("[CLEARSPMIMAGE] GROQ_API_KEY chưa được cấu hình")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        user_content: list[dict] = [
            {
                "type": "text",
                "text": "Phân loại các ảnh theo system prompt. Chỉ dựa vào ảnh.",
            },
            *image_blocks,
        ]
        payload = {
            "model": GROQ_VISION_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.05,
            "max_tokens": 300,
        }

        raw = ""
        try:
            async with aiohttp.ClientSession(timeout=_GROQ_HTTP_TIMEOUT) as session:
                async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error("[CLEARSPMIMAGE] Groq API %s: %s", resp.status, err)
                        return None
                    data = await resp.json()
                    raw = (data["choices"][0]["message"]["content"] or "").strip()
        except asyncio.TimeoutError:
            logger.error("[CLEARSPMIMAGE] Groq hết thời gian chờ")
            return None
        except Exception as e:
            logger.error("[CLEARSPMIMAGE] Lỗi gọi Groq: %s", e)
            return None

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("[CLEARSPMIMAGE] JSON Groq không hợp lệ: %s | raw=%r", e, raw[:500])
            return None

    async def _notify(
        self,
        channel: discord.abc.Messageable,
        embed: discord.Embed,
        delete_after: float = 25.0,
    ):
        try:
            msg = await channel.send(embed=embed)
            await asyncio.sleep(delete_after)
            try:
                await msg.delete()
            except discord.NotFound:
                pass
        except discord.Forbidden:
            logger.warning("[CLEARSPMIMAGE] Không gửi được thông báo tại channel %s", channel.id)
        except Exception as e:
            logger.error("[CLEARSPMIMAGE] Lỗi gửi thông báo: %s", e)

    async def _apply_violation(
        self,
        message: discord.Message,
        member: discord.Member,
        category: str,
        reason: str,
    ):
        channel = message.channel
        if not isinstance(channel, _MESSAGEABLE_GUILD):
            logger.warning("[CLEARSPMIMAGE] Bỏ qua: loại kênh %s", type(channel).__name__)
            return

        audit_reason = f"[Ảnh AI Groq] {category}: {reason}"[:450]

        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            logger.warning("[CLEARSPMIMAGE] Bot không xóa được tin tại #%s", channel.id)
            await self._notify(
                channel,
                discord.Embed(
                    title="Ảnh vi phạm (AI)",
                    description=(
                        f"**Người gửi:** {member.mention}\n"
                        f"**Hạng mục:** `{category}`\n"
                        f"**Lý do:** {reason}\n\n"
                        "Bot không có quyền xóa tin — kiểm tra quyền **Manage Messages**."
                    ),
                    color=discord.Color.dark_red(),
                ),
            )
            return

        logger.info(
            "[CLEARSPMIMAGE] Đã xóa tin ảnh vi phạm | user=%s | #%s | %s | %s",
            member.id,
            channel.id,
            category,
            reason[:200],
        )

        if member.guild_permissions.administrator:
            await self._notify(
                channel,
                discord.Embed(
                    title="Ảnh vi phạm (AI) — không timeout Admin",
                    description=(
                        f"**Người gửi:** {member.mention}\n"
                        f"**Hạng mục:** `{category}`\n"
                        f"**Lý do:** {reason}\n\n"
                        "Tin đã xóa. Không áp timeout với Administrator."
                    ),
                    color=discord.Color.orange(),
                ),
            )
            return

        try:
            await member.timeout(TIMEOUT_DURATION, reason=audit_reason)
        except discord.Forbidden:
            await self._notify(
                channel,
                discord.Embed(
                    title="Ảnh vi phạm (AI)",
                    description=(
                        f"Tin đã xóa. **Không timeout được** {member.mention} — cần quyền **Moderate Members**.\n"
                        f"**Hạng mục:** `{category}`\n**Lý do:** {reason}"
                    ),
                    color=discord.Color.orange(),
                ),
            )
            logger.error("[CLEARSPMIMAGE] Không timeout được %s", member)
            return
        except Exception as e:
            await self._notify(
                channel,
                discord.Embed(
                    title="Lỗi khi timeout (ảnh AI)",
                    description=f"{member.mention}: {e}\n**Lý do phát hiện:** {reason}",
                    color=discord.Color.dark_red(),
                ),
            )
            logger.error("[CLEARSPMIMAGE] Lỗi timeout %s: %s", member, e)
            return

        hours = int(TIMEOUT_DURATION.total_seconds() // 3600)
        await self._notify(
            channel,
            discord.Embed(
                title="Đã timeout — ảnh vi phạm (Groq)",
                description=(
                    f"**Người vi phạm:** {member.mention}\n"
                    f"**Hành động:** Đã xóa tin chứa ảnh không phù hợp.\n"
                    f"**Timeout:** {hours} giờ\n"
                    f"**Hạng mục:** `{category}`\n"
                    f"**Lý do (ghi trong audit log timeout):** {reason}"
                ),
                color=discord.Color.red(),
            ),
        )

    async def _handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return

        candidate_atts = _collect_candidate_attachments(message)
        if not candidate_atts:
            return

        has_forward = bool(getattr(message, "message_snapshots", None))
        has_reply_attach = False
        ref = getattr(message, "reference", None)
        if ref is not None:
            resolved = getattr(ref, "resolved", None)
            if isinstance(resolved, discord.Message) and resolved.attachments:
                has_reply_attach = True

        logger.info(
            "[CLEARSPMIMAGE] 📎 tin có attachment | user=%s | #%s | direct=%s | forward=%s | reply_att=%s | files=%s",
            member,
            message.channel.id,
            len(message.attachments),
            has_forward,
            has_reply_attach,
            [a.filename for a in candidate_atts],
        )

        if _should_bypass(member):
            logger.info(
                "[CLEARSPMIMAGE] skip: user %s có quyền admin/manage_messages (bypass)",
                member,
            )
            return

        if not _channel_matches_watch(message, _watch_channel_ids()):
            logger.info(
                "[CLEARSPMIMAGE] skip: channel %s KHÔNG nằm trong watch %s",
                message.channel.id,
                sorted(_watch_channel_ids()),
            )
            return

        if _require_mention() and self.bot.user not in message.mentions:
            logger.info(
                "[CLEARSPMIMAGE] skip: tin không @bot (require_mention=True) | channel=%s",
                message.channel.id,
            )
            return

        logger.info(
            "[CLEARSPMIMAGE] ▶ bắt đầu quét AI | user=%s | #%s",
            member,
            message.channel.id,
        )

        async with _typing_if_supported(message.channel):
            image_blocks = await _read_image_attachments(message)
            if not image_blocks:
                logger.info(
                    "[CLEARSPMIMAGE] skip: không có ảnh hợp lệ trong %s attachment(s) | ct=%s",
                    len(message.attachments),
                    [a.content_type for a in message.attachments],
                )
                return

            logger.info("[CLEARSPMIMAGE] ↗ gọi Groq với %s ảnh…", len(image_blocks))
            result = await self._groq_classify_images(image_blocks)
            if not result:
                return

            violation = _truthy(result.get("violation"))
            category = str(result.get("category", "clean")).strip().lower()
            reason = str(result.get("reason", "Không rõ")).strip() or "Không rõ"

            logger.info(
                "[CLEARSPMIMAGE] Groq → violation=%s category=%s reason=%s",
                violation,
                category,
                reason[:200],
            )

            if not violation or category not in ("sex", "scam"):
                return

            await self._apply_violation(message, member, category, reason)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            await self._handle_message(message)
        except Exception as e:
            logger.exception("[CLEARSPMIMAGE] Lỗi on_message: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClearSpmImage(bot))
    logger.info(
        "[CLEARSPMIMAGE] Cog đã load | kênh: %s | require_mention=%s | model=%s",
        sorted(_watch_channel_ids()),
        _require_mention(),
        GROQ_VISION_MODEL,
    )
