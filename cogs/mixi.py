# -*- coding: utf-8 -*-
"""
Cog Mixi: đặt trạng thái (presence) khi bot sẵn sàng.
Có thể chạy như extension (python bot.py) hoặc tự mình (python cogs/mixi.py) từ thư mục gốc dự án.
"""

import asyncio
import logging
import sys

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# Văn bản hiển thị (custom status hoặc Playing).
STATUS_TEXT = "Các vấn đề về gỡ timeout vui lòng liên hệ manager, nếu bị nhầm"


async def apply_mixi_presence(client: discord.Client) -> None:
    """
    Ưu tiên custom status; nếu API/phiên bản từ chối thì dùng kiểu Playing cùng nội dung.
    """
    # Thử dùng custom activity (hiển thị như "custom status" trên client hỗ trợ)
    try:
        activity: discord.BaseActivity = discord.CustomActivity(name=STATUS_TEXT)
        await client.change_presence(activity=activity, status=discord.Status.online)
        logger.info("Đã đặt presence: Custom — %r", STATUS_TEXT)
        return
    except (TypeError, ValueError) as e:
        logger.debug("CustomActivity không dùng được: %s", e)
    except (discord.HTTPException, discord.DiscordException) as e:
        # Ví dụ: gateway/API không chấp nhận custom cho loại tài khoản này
        logger.warning("Thử CustomActivity thất bại: %s", e)

    # Dự phòng: dạng "Playing …" (luôn hợp lệ với hầu hết bot)
    try:
        await client.change_presence(
            activity=discord.Game(name=STATUS_TEXT),
            status=discord.Status.online,
        )
        logger.info("Đã đặt presence: Playing — %r", STATUS_TEXT)
    except Exception as e:
        logger.error("Không thể đặt presence: %s", e)


class MixiPresence(commands.Cog):
    """
    Cog: lắng nghe on_ready và cập nhật presence.
    (on_ready có thể gọi nhiều lần khi reconnect; set lại presence là ổn định.)
    """

    def __init__(self, bot: commands.Bot) -> None:
        # Giữ tham chiếu bot (commands.Bot) để dùng trong listener
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Chỉ xử lý khi session đã có user (tránh edge case tải sớm)
        if not self.bot.user:
            return
        await apply_mixi_presence(self.bot)


# ─── Tích hợp extension (bot chính load: await load_extension("cogs.mixi")) ─


async def setup(bot: commands.Bot) -> None:
    # Điểm vào chuẩn discord.py: đăng ký cog với instance bot
    await bot.add_cog(MixiPresence(bot))


# ─── Chạy file này trực tiếp: bot tối giản + login ─
#   Chạy từ thư mục gốc:  python cogs/mixi.py
#   (cần biến TOKEN trong config.py hoặc môi trường DISCORD_TOKEN)


def _get_token() -> str:
    try:
        import config

        t = getattr(config, "TOKEN", None)
        if t:
            return str(t)
    except Exception:
        pass
    import os

    t = os.environ.get("DISCORD_TOKEN")
    if t:
        return t
    print(
        "Thiếu token: tạo config.TOKEN (config.py) hoặc biến môi trường DISCORD_TOKEN",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _amain() -> None:
        # Intent tối thiểu cho client bot (presence không cần thêm message intent)
        intents = discord.Intents.default()
        bot = commands.Bot(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=None,
        )

        @bot.event
        async def on_ready() -> None:
            await apply_mixi_presence(bot)
            u = bot.user
            if u is not None:
                print(f"Đã đăng nhập: {u} ({u.id})")

        token = _get_token()
        await bot.start(token)

    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
