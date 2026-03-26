import discord
from discord.ext import commands
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

ALLOWED_ROLE_ID = 1185158470958333953


def _chunk_lines(lines: list[str], max_chars: int = 1800) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + 1
        if buf and size + add > max_chars:
            chunks.append("\n".join(buf))
            buf = [line]
            size = len(line) + 1
        else:
            buf.append(line)
            size += add
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _get_timeout_value(diff) -> object:
    """Thử lấy giá trị timeout từ AuditLogDiff, hỗ trợ nhiều tên attribute."""
    if diff is None:
        return None
    for attr in ("timed_out_until", "communication_disabled_until"):
        val = getattr(diff, attr, None)
        if val is not None:
            return val
    return None


class TimeoutReason(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="2timeout")
    async def two_timeout(self, ctx: commands.Context):
        if not any(r.id == ALLOWED_ROLE_ID for r in getattr(ctx.author, "roles", [])):
            return

        if ctx.guild is None:
            return

        now = datetime.now(timezone.utc)
        timed_out_members: list[discord.Member] = []
        for m in ctx.guild.members:
            until = getattr(m, "timed_out_until", None)
            if until is None:
                continue
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                timed_out_members.append(m)

        if not timed_out_members:
            await ctx.reply("✅ Không có ai đang bị timeout!", mention_author=False)
            return

        timed_out_ids = {m.id for m in timed_out_members}
        reason_by_id: dict[int, str] = {}

        try:
            async for entry in ctx.guild.audit_logs(
                limit=300,
                action=discord.AuditLogAction.member_update,
            ):
                target = getattr(entry, "target", None)
                if not target or target.id not in timed_out_ids:
                    continue
                if target.id in reason_by_id:
                    continue

                after_val = _get_timeout_value(getattr(entry, "after", None))

                if after_val is not None:
                    reason_by_id[target.id] = entry.reason or "Không có lý do"
                    logger.info(f"[2timeout] Found reason for {target}: {entry.reason!r}")

                if len(reason_by_id) == len(timed_out_ids):
                    break

        except discord.Forbidden:
            logger.warning("[2timeout] Bot thiếu quyền View Audit Log")
        except Exception as e:
            logger.error(f"[2timeout] Lỗi đọc audit log: {e}")

        lines: list[str] = []
        for m in sorted(timed_out_members, key=lambda x: x.timed_out_until or now):
            until = m.timed_out_until
            if until is None:
                continue
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            remaining = until - now
            total_sec = int(remaining.total_seconds())
            if total_sec < 0:
                continue
            days, rem = divmod(total_sec, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)
            parts = []
            if days:
                parts.append(f"{days}d")
            if hours:
                parts.append(f"{hours}h")
            if minutes:
                parts.append(f"{minutes}m")
            if not parts:
                parts.append(f"{seconds}s")
            remaining_str = " ".join(parts)

            reason = reason_by_id.get(m.id, "Không rõ")
            lines.append(f"{m.mention} | ⏱ còn lại: **{remaining_str}** | 📝 lý do: {reason}")

        chunks = _chunk_lines(lines, max_chars=1800)
        for i, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title="⏳ Danh sách đang bị timeout",
                description=chunk,
                color=discord.Color.orange(),
                timestamp=now,
            )
            embed.set_footer(
                text=f"Trang {i}/{len(chunks)} • Yêu cầu bởi {ctx.author.display_name}"
                if len(chunks) > 1
                else f"Yêu cầu bởi {ctx.author.display_name}"
            )
            await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(TimeoutReason(bot))
