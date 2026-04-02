import discord
from discord.ext import commands
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

ALLOWED_ROLE_ID = 1185158470958333953


class RemoveTimeout(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="untimeout")
    async def untimeout_all(self, ctx: commands.Context):
        """Xoá timeout của tất cả thành viên đang bị timeout trong server."""
        if not any(r.id == ALLOWED_ROLE_ID for r in getattr(ctx.author, "roles", [])):
            return

        if ctx.guild is None:
            return

        now = datetime.now(timezone.utc)
        timed_out: list[discord.Member] = []
        for m in ctx.guild.members:
            until = getattr(m, "timed_out_until", None)
            if until is None:
                continue
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                timed_out.append(m)

        if not timed_out:
            await ctx.reply("✅ Không có ai đang bị timeout!", mention_author=False)
            return

        msg = await ctx.reply(
            f"⏳ Đang xoá timeout cho **{len(timed_out)}** thành viên...",
            mention_author=False,
        )

        success: list[discord.Member] = []
        failed: list[tuple[discord.Member, str]] = []

        for m in timed_out:
            try:
                await m.timeout(None, reason=f"Xoá timeout hàng loạt bởi {ctx.author}")
                success.append(m)
            except discord.Forbidden:
                failed.append((m, "Thiếu quyền hoặc role cao hơn bot"))
            except Exception as e:
                failed.append((m, str(e)))

        lines: list[str] = []
        if success:
            names = ", ".join(m.mention for m in success[:30])
            if len(success) > 30:
                names += f" ... và {len(success) - 30} người khác"
            lines.append(f"✅ Đã xoá timeout **{len(success)}** người:\n{names}")
        if failed:
            fail_lines = "\n".join(
                f"❌ {m.mention} — {reason}" for m, reason in failed[:20]
            )
            lines.append(f"\n⚠️ Không xoá được **{len(failed)}** người:\n{fail_lines}")

        embed = discord.Embed(
            title="🔓 Xoá timeout hàng loạt",
            description="\n".join(lines),
            color=discord.Color.green() if not failed else discord.Color.orange(),
            timestamp=now,
        )
        embed.set_footer(text=f"Yêu cầu bởi {ctx.author.display_name}")

        try:
            await msg.edit(content=None, embed=embed)
        except discord.HTTPException:
            await ctx.reply(embed=embed, mention_author=False)

        logger.info(
            "[untimeout] user=%s guild=%s success=%d failed=%d",
            ctx.author.id, ctx.guild.id, len(success), len(failed),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RemoveTimeout(bot))
