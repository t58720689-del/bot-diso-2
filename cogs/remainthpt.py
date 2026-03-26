import discord
from discord.ext import commands
from discord import app_commands
import datetime
import json
import os

# ======================== CONFIG ========================
THPT_DATE = datetime.date(2026, 6, 11)  # Sẽ được cập nhật sau (datetime.date(2026, 6, 26))
# ========================================================

def get_allowed_channels():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("remainthpt_channels", [])
    except:
        return []

def save_allowed_channel(channel_id: int):
    config = {}
    if os.path.exists("config.json"):
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    channels = config.get("remainthpt_channels", [])
    if channel_id not in channels:
        channels.append(channel_id)
    config["remainthpt_channels"] = channels
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def remove_allowed_channel(channel_id: int):
    config = {}
    if os.path.exists("config.json"):
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    channels = config.get("remainthpt_channels", [])
    if channel_id in channels:
        channels.remove(channel_id)
    config["remainthpt_channels"] = channels
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def build_remain_embed(ctx_or_interaction=None) -> discord.Embed:
    today = datetime.date.today()

    if THPT_DATE is None:
        embed = discord.Embed(
            title="📅 Đếm Ngược Kỳ Thi THPT Quốc Gia",
            description=(
                "```\n"
                "⚠️  Ngày thi chưa được cấu hình!\n"
                "```"
            ),
            color=0xFF6B6B
        )
        embed.set_footer(text="Vui lòng liên hệ Admin để cập nhật ngày thi.")
        return embed

    delta = THPT_DATE - today
    days_left = delta.days

    # Tính tuần & ngày lẻ
    weeks = days_left // 7
    remain_days = days_left % 7

    # Màu sắc theo mức độ
    if days_left <= 0:
        color = 0x2ECC71   # Xanh lá - đã thi xong / hôm nay
    elif days_left <= 30:
        color = 0xFF6B6B   # Đỏ - sắp thi
    elif days_left <= 90:
        color = 0xF39C12   # Cam - còn ít
    else:
        color = 0x3498DB   # Xanh dương - còn nhiều

    # Thanh tiến trình
    if days_left <= 0:
        bar = "█" * 20
        percent = 100
    else:
        start_date = datetime.date(today.year, 1, 1)
        total = (THPT_DATE - start_date).days or 1
        elapsed = (today - start_date).days
        percent = min(int(elapsed / total * 100), 100)
        filled = int(percent / 5)
        bar = "█" * filled + "░" * (20 - filled)

    if days_left < 0:
        status_line = "🎉  Kỳ thi đã kết thúc!"
        countdown_text = f"Đã qua **{abs(days_left)}** ngày kể từ kỳ thi."
    elif days_left == 0:
        status_line = "🔥  HÔM NAY LÀ NGÀY THI!"
        countdown_text = "Chúc các bạn thí sinh thi thật tốt! 💪"
    else:
        status_line = f"⏳  Còn {days_left} ngày nữa!"
        countdown_text = f"Tương đương **{weeks} tuần** và **{remain_days} ngày**"

    embed = discord.Embed(
        title="📚 Đếm Ngược Kỳ Thi THPT Quốc Gia 2026",
        color=color,
        timestamp=datetime.datetime.utcnow()
    )

    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━━━",
        value=(
            f"```\n"
            f"{status_line}\n"
            f"```"
        ),
        inline=False
    )

    embed.add_field(
        name="📆  Ngày thi",
        value=f"**{THPT_DATE.strftime('%d/%m/%Y')}**",
        inline=True
    )

    embed.add_field(
        name="📅  Hôm nay",
        value=f"**{today.strftime('%d/%m/%Y')}**",
        inline=True
    )

    embed.add_field(
        name="\u200b",
        value="\u200b",
        inline=True
    )

    embed.add_field(
        name="⏱️  Thời gian còn lại",
        value=countdown_text,
        inline=False
    )

    embed.add_field(
        name=f"📊  Tiến trình năm học  •  {percent}%",
        value=f"```\n[{bar}] {percent}%\n```",
        inline=False
    )

    # Motivational quote
    if days_left > 90:
        quote = "💡 *\"Hành trình ngàn dặm bắt đầu từ một bước chân.\"*"
    elif days_left > 30:
        quote = "📖 *\"Nước rút rồi! Hãy tập trung ôn luyện thật tốt!\"*"
    elif days_left > 0:
        quote = "🔥 *\"Sắp đến đích rồi! Cố lên nào!\"*"
    else:
        quote = "🎊 *\"Chúc mừng các bạn đã vượt qua kỳ thi!\"*"

    embed.add_field(name="\u200b", value=quote, inline=False)

    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1472557179985727710/1479583740819800197/123136468_2691540544401679_7865410719737044269_n.jpg?ex=69ac913d&is=69ab3fbd&hm=921659af75ba558aaab9faacc9e22d0d65f36544990d1ad2b8c478d9fa79e54c&")
    embed.set_footer(
        text="📌 THPT Quốc Gia 2026  •  Cập nhật lúc",
        icon_url="https://cdn.discordapp.com/attachments/1472557179985727710/1479583740819800197/123136468_2691540544401679_7865410719737044269_n.jpg?ex=69ac913d&is=69ab3fbd&hm=921659af75ba558aaab9faacc9e22d0d65f36544990d1ad2b8c478d9fa79e54c&"
    )

    return embed


class RemainTHPT(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── PREFIX COMMAND ───────────────────────────────────────────────────────

    @commands.command(name="remainthpt", aliases=["countdown", "thpt"])
    async def remainthpt_prefix(self, ctx: commands.Context):
        """Đếm ngược ngày thi THPT Quốc Gia"""
        allowed = get_allowed_channels()

        if allowed and ctx.channel.id not in allowed:
            embed = discord.Embed(
                title="🚫 Không được phép",
                description=f"Lệnh này chỉ được dùng trong các kênh được chỉ định!\nDùng `!setremainch` để thêm kênh.",
                color=0xFF6B6B
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        embed = build_remain_embed()
        await ctx.send(embed=embed)

    # ─── SLASH COMMAND ────────────────────────────────────────────────────────

    @app_commands.command(name="remainthpt", description="📅 Xem đếm ngược ngày thi THPT Quốc Gia")
    async def remainthpt_slash(self, interaction: discord.Interaction):
        allowed = get_allowed_channels()

        if allowed and interaction.channel_id not in allowed:
            embed = discord.Embed(
                title="🚫 Không được phép",
                description="Lệnh này chỉ được dùng trong các kênh được chỉ định!",
                color=0xFF6B6B
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = build_remain_embed()
        await interaction.response.send_message(embed=embed)

    # ─── QUẢN LÝ KÊNH (ADMIN) ─────────────────────────────────────────────────

    @commands.command(name="setremainch")
    @commands.has_permissions(administrator=True)
    async def set_remain_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """[Admin] Thêm kênh cho phép dùng lệnh remainthpt"""
        target = channel or ctx.channel
        save_allowed_channel(target.id)

        embed = discord.Embed(
            title="✅ Đã thêm kênh",
            description=f"Kênh {target.mention} đã được cho phép dùng lệnh `!remainthpt`.",
            color=0x2ECC71
        )
        await ctx.send(embed=embed)

    @commands.command(name="removeremainch")
    @commands.has_permissions(administrator=True)
    async def remove_remain_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """[Admin] Xóa kênh khỏi danh sách cho phép"""
        target = channel or ctx.channel
        remove_allowed_channel(target.id)

        embed = discord.Embed(
            title="🗑️ Đã xóa kênh",
            description=f"Kênh {target.mention} đã bị xóa khỏi danh sách cho phép.",
            color=0xE74C3C
        )
        await ctx.send(embed=embed)

    @commands.command(name="listremainch")
    @commands.has_permissions(administrator=True)
    async def list_remain_channels(self, ctx: commands.Context):
        """[Admin] Xem danh sách kênh cho phép"""
        channels = get_allowed_channels()

        if not channels:
            desc = "Chưa có kênh nào được cấu hình.\n*(Tất cả kênh đều được dùng)*"
        else:
            desc = "\n".join(
                [f"• <#{cid}> (`{cid}`)" for cid in channels]
            )

        embed = discord.Embed(
            title="📋 Danh sách kênh cho phép `!remainthpt`",
            description=desc,
            color=0x3498DB
        )
        await ctx.send(embed=embed)

    # ─── ERROR HANDLERS ───────────────────────────────────────────────────────

    @set_remain_channel.error
    @remove_remain_channel.error
    @list_remain_channels.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                title="🚫 Thiếu quyền",
                description="Bạn cần quyền **Administrator** để dùng lệnh này!",
                color=0xFF6B6B
            )
            await ctx.send(embed=embed, delete_after=5)


async def setup(bot):
    await bot.add_cog(RemainTHPT(bot))