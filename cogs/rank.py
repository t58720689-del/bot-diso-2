import discord
from discord.ext import commands

from utils.helpers import is_allowed_channel
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Chỉ người gõ !ranking có ít nhất một trong các role này mới được dùng lệnh.
# Thay bằng ID role thật trên server của bạn.
RANKING_INVOKER_ROLE_IDS: tuple[int, ...] = (1185158470958333953,1241969973086388244)

# Quy tắc điểm: mỗi (role_id, điểm cộng) — có role đó thì cộng điểm tương ứng.
RANK_ROLE_RULES: list[tuple[int, int]] = [
    (1495057056057790504, 1),
    (1481297073969037353, 1),
    (1482031406468304926, 2),
    (1481296840988168303, 1),
    (1494435102317347077, 1),
    

]


def _member_role_ids(member: discord.Member) -> set[int]:
    return {role.id for role in member.roles}


def _can_invoke_ranking(member: discord.Member) -> bool:
    have = _member_role_ids(member)
    return any(rid in have for rid in RANKING_INVOKER_ROLE_IDS)


def compute_rank_score(member: discord.Member) -> tuple[int, list[tuple[int, int, str]]]:
    """
    Trả về (tổng điểm, chi tiết).
    Chi tiết: list (role_id, điểm, tên hiển thị).
    """
    have = _member_role_ids(member)
    guild = member.guild
    total = 0
    rows: list[tuple[int, int, str]] = []
    for role_id, pts in RANK_ROLE_RULES:
        if role_id not in have:
            continue
        total += pts
        role_obj = guild.get_role(role_id) if guild else None
        label = role_obj.name if role_obj else "Role"
        rows.append((role_id, pts, label))
    return total, rows


class Rank(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ranking")
    @commands.guild_only()
    @is_allowed_channel()
    async def ranking_cmd(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        """!ranking [@thành_viên] — xem điểm theo role (quy tắc trong file này)."""
        author = ctx.author
        if not isinstance(author, discord.Member):
            await ctx.send("⚠️ Chỉ dùng trong server.", delete_after=8)
            return
        if not _can_invoke_ranking(author):
            await ctx.send("⚠️ Bạn không có quyền dùng lệnh này.", delete_after=8)
            return

        target = member or author
        if not isinstance(target, discord.Member):
            await ctx.send("⚠️ Chỉ dùng trong server.", delete_after=8)
            return

        total, matched = compute_rank_score(target)

        embed = discord.Embed(
            title="Điểm thành viên (theo role)",
            color=discord.Color.blurple(),
        )
        embed.set_author(
            name=target.display_name,
            icon_url=target.display_avatar.replace(size=64).url,
        )
        embed.add_field(name="Tổng điểm", value=f"**{total}**", inline=True)
        embed.add_field(name="Thành viên", value=target.mention, inline=True)

        if not matched:
            g = ctx.guild
            rule_labels: list[str] = []
            for rid, _ in RANK_ROLE_RULES:
                ro = g.get_role(rid) if g else None
                if ro:
                    rule_labels.append(ro.name)
            if rule_labels:
                lines = "Không khớp role nào trong quy tắc.\nCác role được tính: " + ", ".join(
                    rule_labels
                )
            else:
                lines = "Không khớp role nào trong quy tắc."
        else:
            lines = "\n".join(
                f"• **{name}** → +{pts}"
                for _rid, pts, name in matched
            )
        embed.add_field(name="Chi tiết", value=lines, inline=False)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rank(bot))
