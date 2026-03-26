import asyncio
import discord
from datetime import timedelta
from discord.ext import commands

# ID role được phép dùng !stop — chỉ cần có **một** trong các role (thêm/bớt ID trong tuple)
# Lưu ý: phải là tuple các số nguyên, không bọc list vào has_role (sẽ luôn báo thiếu role).
STOP_ALLOWED_ROLE_IDS = (
    1469581542841122918,
    1185158470958333953,
)


def _member_name_variants(member: discord.Member) -> set[str]:
    names: set[str] = set()
    for n in (member.display_name, member.name, getattr(member, "global_name", None)):
        if n:
            names.add(n.lower())
    return names


def _find_member_by_query(guild: discord.Guild, query: str) -> tuple[discord.Member | None, str]:
    """Trả về (member, '') hoặc (None, 'not_found'|'ambiguous')."""
    q = query.strip().lower()
    if not q:
        return None, "not_found"

    exact: set[discord.Member] = set()
    partial: set[discord.Member] = set()

    for m in guild.members:
        variants = _member_name_variants(m)
        if q in variants:
            exact.add(m)
            continue
        if any(q in v for v in variants):
            partial.add(m)

    if len(exact) == 1:
        return next(iter(exact)), ""
    if len(exact) > 1:
        return None, "ambiguous"
    if len(partial) == 1:
        return next(iter(partial)), ""
    if len(partial) > 1:
        return None, "ambiguous"
    return None, "not_found"


async def _delete_pair_after(author_msg: discord.Message, bot_msg: discord.Message, delay: float) -> None:
    await asyncio.sleep(delay)
    for m in (author_msg, bot_msg):
        try:
            await m.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class StopCog(commands.Cog):
    """Timeout thành viên theo tên / mention / ID."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply_auto_delete(self, ctx: commands.Context, content: str) -> None:
        bot_msg = await ctx.send(content)
        asyncio.create_task(_delete_pair_after(ctx.message, bot_msg, 5.0))

    @commands.command(name="stop")
    @commands.guild_only()
    @commands.has_any_role(*STOP_ALLOWED_ROLE_IDS)
    @commands.bot_has_permissions(moderate_members=True, manage_messages=True)
    async def stop_timeout(
        self,
        ctx: commands.Context,
        *,
        target: str | None = None,
    ):
        """`!stop @user` hoặc `!stop tên` — mặc định 10 phút; `!stop @user 30` = 30 phút (tối đa 28 ngày)."""
        if not target or not target.strip():
            await self._reply_auto_delete(
                ctx,
                "Cách dùng: `!stop @người_dùng` hoặc `!stop tên` — thêm số phút ở cuối (vd: `!stop @user 30`).",
            )
            return

        member: discord.Member | None = None
        parts = target.strip().split()
        minutes = 10
        if len(parts) >= 2 and parts[-1].isdigit():
            minutes = max(1, min(int(parts[-1]), 28 * 24 * 60))
            parts = parts[:-1]
        text = " ".join(parts)
        if not text:
            await self._reply_auto_delete(ctx, "Thiếu đối tượng. Ví dụ: `!stop @user` hoặc `!stop @user 60`.")
            return

        if ctx.message.mentions:
            member = ctx.message.mentions[0]
        elif text.isdigit():
            uid = int(text)
            member = ctx.guild.get_member(uid)
            if member is None:
                try:
                    member = await ctx.guild.fetch_member(uid)
                except discord.NotFound:
                    member = None
        if member is None:
            try:
                member = await commands.MemberConverter().convert(ctx, text)
            except commands.BadArgument:
                found, err = _find_member_by_query(ctx.guild, text)
                if err == "ambiguous":
                    await self._reply_auto_delete(
                        ctx,
                        "Có nhiều người khớp tên. Dùng **mention** (`@tên`) hoặc **ID** để chọn đúng người.",
                    )
                    return
                member = found

        if member is None:
            await self._reply_auto_delete(
                ctx, "Không tìm thấy thành viên. Thử mention, ID, hoặc tên chính xác hơn."
            )
            return

        if member.id == ctx.guild.owner_id:
            await self._reply_auto_delete(ctx, "Không thể timeout chủ server.")
            return
        if member.id == ctx.author.id:
            await self._reply_auto_delete(ctx, "Bạn không thể timeout chính mình.")
            return
        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            await self._reply_auto_delete(ctx, "Bạn không thể timeout người có vai trò cao hơn hoặc ngang bạn.")
            return
        if member.top_role >= ctx.guild.me.top_role:
            await self._reply_auto_delete(ctx, "Bot không thể timeout người có vai trò cao hơn bot.")
            return

        delta = timedelta(minutes=minutes)
        reason = f"!stop bởi {ctx.author} ({minutes} phút)"
        try:
            await member.timeout(delta, reason=reason)
        except discord.Forbidden:
            await self._reply_auto_delete(
                ctx, "Bot không đủ quyền **Moderate Members** (hoặc không áp dụng được với người này)."
            )
            return
        except discord.HTTPException as e:
            await self._reply_auto_delete(ctx, f"Discord từ chối thao tác: `{e}`")
            return

        await self._reply_auto_delete(
            ctx,
            f"Đã timeout {member.mention} **{minutes} phút** — áp dụng cả server (mọi kênh chat/voice).",
        )

    @stop_timeout.error
    async def stop_timeout_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, (commands.MissingRole, commands.MissingAnyRole)):
            await self._reply_auto_delete(
                ctx, "Bạn cần có **một trong các role** được cấu hình trong file `stop.py` mới dùng được `!stop`."
            )
            return
        elif isinstance(error, commands.BotMissingPermissions):
            await self._reply_auto_delete(
                ctx,
                "Bot thiếu quyền **Moderate Members** hoặc **Manage Messages** (cần để timeout và xóa tin nhắn lệnh).",
            )
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(StopCog(bot))
