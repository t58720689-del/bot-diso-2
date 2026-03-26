import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import config
from utils.logger import setup_logger

logger = setup_logger(__name__)


class Donate(commands.Cog):
    # Nội dung donate message
    DONATE_DESCRIPTION = (
        "Ủng hộ bot tại"
        " [**đây**]({link})"
    )
    DONATE_FOOTER = "Chúc mọi người có một ngày mới thật vui vẻ❤️"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Lưu timestamp tin nhắn gần nhất cho mỗi channel: {channel_id: [datetime, ...]}
        self.message_timestamps: dict[int, list[datetime]] = defaultdict(list)
        # Cooldown: thời điểm cuối cùng gửi donate cho mỗi channel
        self.last_donate_sent: dict[int, datetime] = {}
        # Lấy cấu hình
        self.trigger_count = getattr(config, 'DONATE_TRIGGER_COUNT', 10)
        self.trigger_window = getattr(config, 'DONATE_TRIGGER_WINDOW', 60)  # giây
        self.cooldown = getattr(config, 'DONATE_COOLDOWN', 3600)  # giây
        # Danh sách channel ID cần theo dõi
        self.channel_ids = (
            config.DONATE_CHANNEL_ID
            if isinstance(config.DONATE_CHANNEL_ID, list)
            else [config.DONATE_CHANNEL_ID]
        )

    def _create_donate_embed(self) -> discord.Embed:
        """Tạo embed donate message."""
        embed = discord.Embed(
            description=self.DONATE_DESCRIPTION.format(link=config.DONATE_LINK),
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=self.DONATE_FOOTER)
        return embed

    def _cleanup_old_timestamps(self, channel_id: int, now: datetime):
        """Xoá các timestamp cũ ngoài cửa sổ thời gian."""
        cutoff = now - timedelta(seconds=self.trigger_window)
        self.message_timestamps[channel_id] = [
            ts for ts in self.message_timestamps[channel_id] if ts >= cutoff
        ]

    def _is_on_cooldown(self, channel_id: int, now: datetime) -> bool:
        """Kiểm tra channel có đang trong cooldown không."""
        last_sent = self.last_donate_sent.get(channel_id)
        if last_sent is None:
            return False
        return (now - last_sent).total_seconds() < self.cooldown

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Theo dõi tin nhắn trong các kênh donate, trigger khi đạt ngưỡng."""
        # Bỏ qua tin nhắn từ bot
        if message.author.bot:
            return

        # Chỉ theo dõi các kênh donate được cấu hình
        if message.channel.id not in self.channel_ids:
            return

        channel_id = message.channel.id
        now = datetime.now(timezone.utc)

        # Ghi nhận timestamp tin nhắn
        self.message_timestamps[channel_id].append(now)

        # Dọn dẹp timestamp cũ
        self._cleanup_old_timestamps(channel_id, now)

        # Kiểm tra đã đạt ngưỡng chưa
        count = len(self.message_timestamps[channel_id])
        if count < self.trigger_count:
            return

        # Kiểm tra cooldown
        if self._is_on_cooldown(channel_id, now):
            return

        # Đạt ngưỡng & không cooldown → gửi donate
        try:
            embed = self._create_donate_embed()
            await message.channel.send(embed=embed)
            self.last_donate_sent[channel_id] = now
            # Reset danh sách timestamp sau khi gửi
            self.message_timestamps[channel_id].clear()
            logger.info(
                f"Donate triggered in channel {channel_id} "
                f"({count} messages in {self.trigger_window}s)"
            )
        except Exception as e:
            logger.error(f"Error sending donate message in channel {channel_id}: {e}")

    # ── Command xem thử donate message ──────────────────────────────────────
    @commands.command(name="test_donate", aliases=["testdonate"])
    async def test_donate(self, ctx: commands.Context):
        """Gửi thử tin nhắn donate để xem trước."""
        allowed_users = [852796371622690856]
        if config.OWNER_ID and config.OWNER_ID.isdigit():
            allowed_users.append(int(config.OWNER_ID))
        if ctx.author.id not in allowed_users:
            await ctx.send("❌ Chỉ owner mới dùng được lệnh này.")
            return

        embed = self._create_donate_embed()
        await ctx.send("🧪 **Preview donate message:**", embed=embed)
        logger.info(f"Test donate sent by {ctx.author} in channel {ctx.channel.id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Donate(bot))
