import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timezone, timedelta
import asyncio
import config
from utils.logger import setup_logger
from cogs.remainthpt import build_remain_embed, get_allowed_channels

logger = setup_logger(__name__)


class Daily2(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_message_task.start()
        self.countdown_task.start()
        self.thpt_reminder_task.start()

    def cog_unload(self):
        self.daily_message_task.cancel()
        self.countdown_task.cancel()
        self.thpt_reminder_task.cancel()

    # ─── Daily morning message ────────────────────────────────────────────

    @tasks.loop(time=[
        time(hour=5, minute=30, tzinfo=timezone(timedelta(hours=7))),  # 5:30 AM GMT+7
    ])
    async def daily_message_task(self):
        """Gửi tin nhắn buổi sáng lúc 5h30 GMT+7."""
        try:
            channel = self.bot.get_channel(config.DAILY_MESSAGE_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title="🌅 Chào Buổi Sáng!",
                    description="Đối thủ của bạn đã học bài rồi đó ☀️",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text="Chào buổi sáng")
                await channel.send(
                    "https://cdn.discordapp.com/attachments/1439553447384060047/1474840324005167347/1.mp4"
                    "?ex=699b4f96&is=6999fe16&hm=12b722843670f961fe8ee6e4c7a25c9a4b86e330450e58be388f0d4e0a2ab083&"
                )
                logger.info(f"Sent daily message to channel {config.DAILY_MESSAGE_CHANNEL_ID}")
            else:
                logger.warning(f"Daily message channel not found: {config.DAILY_MESSAGE_CHANNEL_ID}")
        except Exception as e:
            logger.error(f"Error sending daily message: {e}")

    @daily_message_task.before_loop
    async def before_daily_message(self):
        """Đợi bot sẵn sàng trước khi bắt đầu daily message."""
        await self.bot.wait_until_ready()
        logger.info("Daily message task ready — will run at 5:30 AM GMT+7")

    # ─── Countdown task ───────────────────────────────────────────────────

    @tasks.loop(time=[
        time(hour=8, minute=0, tzinfo=timezone(timedelta(hours=7))),   # 8:00 AM GMT+7
        time(hour=23, minute=0, tzinfo=timezone(timedelta(hours=7))),  # 23:00 PM GMT+7
    ])
    async def countdown_task(self):
        """Gửi đếm ngược lúc 8h00 và 23h00 GMT+7."""
        try:
            target = datetime.strptime(config.COUNTDOWN_TARGET_DATE, "%Y-%m-%d")

            now_utc = datetime.now(timezone.utc)
            gmt7 = timezone(timedelta(hours=7))
            now_gmt7 = now_utc.astimezone(gmt7)
            today = now_gmt7.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)

            days_left = (target - today).days

            if days_left < 0:
                embed = discord.Embed(
                    title=f"🎉 {config.COUNTDOWN_EVENT_NAME}",
                    description=f"Sự kiện **{config.COUNTDOWN_EVENT_NAME}** đã diễn ra rồi!",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text="Sự kiện đã qua")
                channel_ids = (
                    config.COUNTDOWN_CHANNEL_ID
                    if isinstance(config.COUNTDOWN_CHANNEL_ID, list)
                    else [config.COUNTDOWN_CHANNEL_ID]
                )
                for channel_id in channel_ids:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send(embed=embed)
                self.countdown_task.stop()
                logger.info("Countdown task stopped — event has passed")
                return

            if days_left == 0:
                embed = discord.Embed(
                    title=f"🔥 HÔM NAY LÀ NGÀY: {config.COUNTDOWN_EVENT_NAME}!",
                    description=(
                        f"⏰ **HÔM NAY** là ngày **{config.COUNTDOWN_EVENT_NAME}**!\n\n"
                        "Chúc mọi người may mắn! 💪🍀"
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text="🔥 Ngày đã đến!")
            else:
                if days_left <= 3:
                    color, urgency = discord.Color.red(), "🚨 Bạn đã chuẩn bị cho đợt 2 ?"
                elif days_left <= 7:
                    color, urgency = discord.Color.orange(), "⚠️ Chỉ còn 1 tuần!"
                elif days_left <= 30:
                    color, urgency = discord.Color.yellow(), "📢 Còn sớm mà cứ chơi đi"
                else:
                    color, urgency = discord.Color.blue(), "📅 Còn khá lâu"

                embed = discord.Embed(
                    title=f"⏳ Đếm ngược: {config.COUNTDOWN_EVENT_NAME}",
                    description=(
                        f"📆 Còn **{days_left}** ngày nữa là đến **{config.COUNTDOWN_EVENT_NAME}**!\n\n"
                        f"{urgency}\n\n"
                        f"🎯 Ngày mục tiêu: **{target.strftime('%d/%m/%Y')}**"
                    ),
                    color=color,
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text="Đếm ngược lúc 8:00 AM & 11:00 PM")

            channel_ids = (
                config.COUNTDOWN_CHANNEL_ID
                if isinstance(config.COUNTDOWN_CHANNEL_ID, list)
                else [config.COUNTDOWN_CHANNEL_ID]
            )
            for channel_id in channel_ids:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(embed=embed)
                    logger.info(f"Sent countdown to channel {channel_id}: {days_left} days left")
                else:
                    logger.warning(f"Countdown channel {channel_id} not found")

        except Exception as e:
            logger.error(f"Error sending countdown message: {e}")

    @countdown_task.before_loop
    async def before_countdown(self):
        """Đợi bot sẵn sàng trước khi bắt đầu countdown."""
        await self.bot.wait_until_ready()
        logger.info("Countdown task ready — will run at 8:00 AM & 11:00 PM GMT+7")

    # ─── THPT Reminder task ───────────────────────────────────────────────

    @tasks.loop(time=[
        time(hour=7, minute=30, tzinfo=timezone(timedelta(hours=7))),   # 7:30 AM GMT+7
        time(hour=23, minute=30, tzinfo=timezone(timedelta(hours=7))),  # 23:30 PM GMT+7
    ])
    async def thpt_reminder_task(self):
        """Gửi đếm ngược THPT lúc 7h30 và 23h30 GMT+7."""
        try:
            embed = build_remain_embed()
            channel_ids = get_allowed_channels()

            if not channel_ids:
                logger.warning("No THPT reminder channels configured, using fallback from config.")
                channel_ids = (
                    config.THPT_REMINDER_CHANNEL_ID
                    if isinstance(config.THPT_REMINDER_CHANNEL_ID, list)
                    else [config.THPT_REMINDER_CHANNEL_ID]
                )

            for channel_id in channel_ids:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(embed=embed)
                    logger.info(f"Sent THPT reminder to channel {channel_id}")
                else:
                    logger.warning(f"THPT reminder channel {channel_id} not found")

        except Exception as e:
            logger.error(f"Error sending THPT reminder: {e}")

    @thpt_reminder_task.before_loop
    async def before_thpt_reminder(self):
        """Đợi bot sẵn sàng trước khi bắt đầu THPT reminder."""
        await self.bot.wait_until_ready()
        logger.info("THPT reminder task ready — will run at 7:30 AM & 11:30 PM GMT+7")


async def setup(bot: commands.Bot):
    await bot.add_cog(Daily2(bot))
