import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

GMT7 = timezone(timedelta(hours=7))


class Remain(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("[Remain] Cog loaded thanh cong")

    # ─── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _days_left(target_date_str: str) -> int:
        target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        today = datetime.now(GMT7).date()
        return (target - today).days

    @staticmethod
    def _build_embed(event_name: str, target_date_str: str, days: int) -> discord.Embed:
        target = datetime.strptime(target_date_str, "%Y-%m-%d")
        date_fmt = target.strftime("%d/%m/%Y")

        if days > 0:
            total_bar = 20
            total_days = 120
            filled = max(0, min(total_bar, round((total_days - days) / total_days * total_bar)))
            bar = "\u2588" * filled + "\u2591" * (total_bar - filled)

            if days >= 60:
                color = discord.Color.green()
                status_emoji = "\U0001f7e2"
                status = "Con kha nhieu thoi gian, hay on tap deu dan!"
            elif days >= 30:
                color = discord.Color.yellow()
                status_emoji = "\U0001f7e1"
                status = "còn hơn 1 tháng nữa!"
            elif days >= 14:
                color = discord.Color.orange()
                status_emoji = "\U0001f7e0"
                status = "Thời gian chỉ còn hơn 2 tuần bạn đã học được bao nhiêu rồi? Cố gắng lên nào!"
            else:
                color = discord.Color.red()
                status_emoji = "\U0001f534"
                status = "Còn rất ít thời gian rồi"

            embed = discord.Embed(
                title="\U0001f4c5 Đếm ngược kỳ thi",
                description=(
                    f"**{event_name}**\n"
                    f"\U0001f4c6 Ngày thi: `{date_fmt}`\n\n"
                    f"{status_emoji} **Còn `{days}` ngày nữa**\n\n"
                    f"`{bar}` {round(filled / total_bar * 100)}%\n\n"
                    f"\U0001f4a1 {status}"
                ),
                color=color,
            )

        elif days == 0:
            embed = discord.Embed(
                title="\U0001f3af Hôm nay là ngày thi!",
                description=(
                    f"**{event_name}**\n"
                    f"\U0001f4c6 Ngay thi: `{date_fmt}`\n\n"
                    "\U0001f340 Chúc bạn thi thật tốt! Bình tĩnh và tự tin nhé!"
                ),
                color=discord.Color.gold(),
            )

        else:
            embed = discord.Embed(
                title="\u2705 kỳ thi đa diễn ra rồi!",
                description=(
                    f"**{event_name}**\n"
                    f"\U0001f4c6 Ngay thi: `{date_fmt}`\n\n"
                    f"Kỳ thi đã diễn ra **{abs(days)} ngày** trước.\n"
                    "Chúc các bạn may mắn! \U0001f389"
                ),
                color=discord.Color.blurple(),
            )

        embed.set_footer(
            text=f"Hom nay: {datetime.now(GMT7).strftime('%d/%m/%Y')} (GMT+7)"
        )
        return embed

    # ─── Slash command ──────────────────────────────────────────────────────

    @app_commands.command(
        name="remain",
        description="Xem con bao nhieu ngay den ky thi"
    )
    async def remain(self, interaction: discord.Interaction):
        logger.info(
            f"[Remain] /remain duoc goi boi {interaction.user} "
            f"(ID: {interaction.user.id}) tai channel {interaction.channel_id}"
        )
        try:
            await interaction.response.defer()
        except discord.NotFound:
            logger.warning("[Remain] Interaction da het han truoc khi defer() — bo qua")
            return

        try:
            event_name = getattr(config, "COUNTDOWN_EVENT_NAME", "Ky thi sap toi")
            target_date = getattr(config, "COUNTDOWN_TARGET_DATE", None)
            logger.info(f"[Remain] event_name={event_name!r} | target_date={target_date!r}")

            if not target_date:
                logger.warning("[Remain] COUNTDOWN_TARGET_DATE chua duoc cau hinh trong config.py")
                return await interaction.followup.send(
                    "Chua cau hinh ngay thi trong config.py (COUNTDOWN_TARGET_DATE).",
                    ephemeral=True,
                )

            days = self._days_left(target_date)
            logger.info(f"[Remain] Con {days} ngay den ky thi")
            embed = self._build_embed(event_name, target_date, days)
            await interaction.followup.send(embed=embed)
            logger.info("[Remain] Gui embed thanh cong")

        except Exception as e:
            logger.error(f"[Remain] Loi khi xu ly /remain: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"Loi: `{e}`", ephemeral=True)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    logger.info("[Remain] setup() duoc goi")
    await bot.add_cog(Remain(bot))


