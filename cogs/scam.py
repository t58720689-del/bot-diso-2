"""Mọi tin trong kênh TRAP_CHANNEL_IDS → timeout 20 ngày + xóa 20 tin gần nhất của người gửi trong kênh đó."""

from __future__ import annotations

from datetime import timedelta

import discord
from discord.ext import commands

from utils.logger import setup_logger

logger = setup_logger(__name__)

# ID kênh (snowflake): Developer Mode → chuột phải kênh → Copy Channel ID.
# Một kênh: bắt buộc có dấu phẩy sau số — (1234567890123456789,) — không được viết (123...) không phẩy (Python sẽ coi là int).
# Nhiều kênh: (111, 222, 333)
TRAP_CHANNEL_IDS: int | tuple[int, ...] = (1411448689192603669,)

_TIMEOUT_DAYS = 20
_DELETE_LAST = 20
_BULK_DELETE_MAX_AGE = timedelta(days=14)

_TRAP_NOTICE = (
    "CẢNH BÁO⚠️ :  KÊNH NÀY NHẰM MỤC ĐÍCH ĐỂ TỰ ĐỘNG BAN BOT VÀ/HOẶC CÁC TÀI KHOẢN TỰ ĐỘNG. "
    "NẾU BẠN NHẮN VÀO KÊNH NÀY SẼ BỊ HỆ THỐNG TỰ ĐỘNG BAN NGAY LẬP TỨC. "
    "Vui lòng rời khỏi đây nếu không biết bạn đang làm gì."
)


def _trap_channel_ids() -> frozenset[int]:
    raw = TRAP_CHANNEL_IDS
    if isinstance(raw, int):
        return frozenset((raw,))
    return frozenset(int(x) for x in raw)


async def _collect_recent_from_author(
    channel: discord.abc.Messageable,
    author_id: int,
    limit: int,
    *,
    trigger: discord.Message | None = None,
) -> list[discord.Message]:
    """Ưu tiên tin vừa gửi (`trigger`), rồi quét history. Không cần history nếu chỉ xóa được trigger."""
    seen: set[int] = set()
    out: list[discord.Message] = []
    if trigger is not None and trigger.author.id == author_id:
        out.append(trigger)
        seen.add(trigger.id)
    try:
        async for msg in channel.history(limit=500):
            if msg.author.id != author_id or msg.id in seen:
                continue
            out.append(msg)
            seen.add(msg.id)
            if len(out) >= limit:
                break
    except discord.Forbidden:
        logger.warning(
            "[SCAM] Bot không đọc được lịch sử kênh %s — chỉ xóa được tin vừa gửi nếu có (cần quyền Read Message History).",
            getattr(channel, "id", "?"),
        )
    return out


async def _delete_messages(channel: discord.abc.Messageable, messages: list[discord.Message]) -> None:
    if not messages:
        return
    now = discord.utils.utcnow()
    fresh: list[discord.Message] = []
    stale: list[discord.Message] = []
    for m in messages:
        if now - m.created_at <= _BULK_DELETE_MAX_AGE:
            fresh.append(m)
        else:
            stale.append(m)

    async def _delete_one(msg: discord.Message) -> None:
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    # Bulk-delete Discord tối thiểu 2 tin; 1 tin phải xóa riêng lẻ.
    if len(fresh) == 1:
        await _delete_one(fresh[0])
    elif len(fresh) > 1:
        for i in range(0, len(fresh), 100):
            chunk = fresh[i : i + 100]
            if len(chunk) == 1:
                await _delete_one(chunk[0])
                continue
            try:
                await channel.delete_messages(chunk)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning("[SCAM] delete_messages chunk failed: %s", e)
                for m in chunk:
                    await _delete_one(m)
    for m in stale:
        await _delete_one(m)


async def _send_trap_notice(channel: discord.abc.Messageable, member: discord.Member) -> None:
    content = f"{member.mention}\n\n{_TRAP_NOTICE}"
    try:
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except discord.HTTPException as e:
        logger.warning("[SCAM] Không gửi được cảnh báo (kênh %s): %s", getattr(channel, "id", "?"), e)


class Scam(commands.Cog):
    """Xử lý kênh cấm chat (auto-timeout + xóa lịch sử gần)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if message.channel.id not in _trap_channel_ids():
            return

        member = message.author
        if not isinstance(member, discord.Member):
            return

        channel = message.channel
        reason = "Tự động: tin trong kênh TRAP_CHANNEL_IDS (cogs/scam.py)"

        try:
            msgs = await _collect_recent_from_author(
                channel, member.id, _DELETE_LAST, trigger=message
            )
            await _delete_messages(channel, msgs)
            if not msgs:
                logger.warning("[SCAM] Không có tin nào để xóa (user=%s channel=%s)", member.id, channel.id)
        except discord.HTTPException as e:
            logger.error("[SCAM] Lỗi HTTP khi xóa tin: %s", e)
        except Exception as e:
            logger.error("[SCAM] Lỗi khi xóa tin: %s", e)

        if member.guild_permissions.administrator:
            logger.info("[SCAM] Bỏ qua timeout — %s là Admin (kênh %s)", member.id, channel.id)
            return

        me = message.guild.me
        if not me or not me.guild_permissions.moderate_members:
            logger.warning("[SCAM] Bot thiếu Moderate Members — không timeout user=%s", member.id)
            return
        if member.id == message.guild.owner_id:
            logger.info("[SCAM] Bỏ qua timeout — chủ server user=%s", member.id)
            return
        if member.top_role >= me.top_role:
            logger.warning("[SCAM] Bỏ qua timeout — vai trò không thấp hơn bot user=%s", member.id)
            return

        try:
            await member.timeout(timedelta(days=_TIMEOUT_DAYS), reason=reason)
            logger.info(
                "[SCAM] Timeout %s ngày — user=%s channel=%s",
                _TIMEOUT_DAYS,
                member.id,
                channel.id,
            )
            await _send_trap_notice(channel, member)
        except discord.Forbidden:
            logger.error("[SCAM] Không có quyền timeout user=%s (Moderate Members / hierarchy)", member.id)
        except discord.HTTPException as e:
            logger.error("[SCAM] HTTP khi timeout user=%s: %s", member.id, e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scam(bot))
