import discord
from discord.ext import commands

# Chỉ cần có một trong các role sau ở **bất kỳ server nào** bot cùng tham gia với bạn
# (khác với has_any_role: chỉ xét role trên đúng server đang gõ lệnh).
IMAGE_ALLOWED_ROLE_IDS = frozenset(
    (
        1185158470958333953,
        1469581542841122918,
    )
)


def _author_has_allowed_role_anywhere(
    bot: commands.Bot, author_id: int, current_guild: discord.Guild | None
) -> bool:
    def member_has_allowed_role(member: discord.Member) -> bool:
        return any(role.id in IMAGE_ALLOWED_ROLE_IDS for role in member.roles)

    if current_guild is not None:
        m = current_guild.get_member(author_id)
        if m is not None and member_has_allowed_role(m):
            return True

    for guild in bot.guilds:
        if current_guild is not None and guild.id == current_guild.id:
            continue
        member = guild.get_member(author_id)
        if member is None:
            continue
        if member_has_allowed_role(member):
            return True
    return False


class ShowAvt(commands.Cog):
    """Hiển thị avatar qua lệnh !image (giới hạn role)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="image")
    @commands.guild_only()
    async def show_avatar(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        """`!image` — avatar của bạn; `!image @user` — avatar người được tag."""
        if not _author_has_allowed_role_anywhere(self.bot, ctx.author.id, ctx.guild):
            await ctx.send("Bạn không có quyền dùng lệnh `!image`.")
            return
        target = member or ctx.author
        embed = discord.Embed(
            title=f"Avatar — {target.display_name}",
            color=target.color if target.color.value else discord.Color.blurple(),
        )
        embed.set_image(url=target.display_avatar.replace(size=4096).url)
        embed.set_footer(text=f"ID: {target.id}")
        await ctx.send(embed=embed)

    @show_avatar.error
    async def show_avatar_error(
        self, ctx: commands.Context, error: commands.CommandError
    ):
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh `!image` chỉ dùng trong server.")
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ShowAvt(bot))
