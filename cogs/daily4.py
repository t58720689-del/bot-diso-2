# -*- coding: utf-8 -*-
"""Nhắc định kỳ (30 phút) về !append vào một kênh; !test12 xem trước thông báo."""

import discord
from discord.ext import commands, tasks

from utils.logger import setup_logger

logger = setup_logger(__name__)

# Kênh nhận tin nhắn định kỳ (snowflake Discord)
DOCUMENT_REMINDER_CHANNEL_ID = 1486759905431130175


def _reminder_embed() -> discord.Embed:
    return discord.Embed(
        title="📚 Thêm tài liệu",
        description=(
            "Bạn có thể thêm tài liệu bằng lệnh **`!append`**.,xem tài liệu lưu bằng lệnh !docs\n\n"
            "Ví dụ: !append https://e.vnexpress.net/news/travel/food-recipes/famous-eateries-tea-milk-chains-in-hanoi-fined-for-food-safety-violations-5068085.html food"
        ),
        color=discord.Color.teal(),
    )


async def _resolve_messageable(
    bot: commands.Bot, channel_id: int
) -> discord.abc.Messageable | None:
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning(
                "[daily4] Không truy cập được kênh %s: %s",
                channel_id,
                e,
            )
            return None
    if not isinstance(ch, discord.abc.Messageable):
        logger.warning("[daily4] ID %s không phải kênh gửi tin được.", channel_id)
        return None
    return ch


class Daily4(commands.Cog):
    """Nhắc !append mỗi 30 phút vào kênh chỉ định."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        if not self.doc_reminder_loop.is_running():
            self.doc_reminder_loop.start()
        logger.info(
            "[daily4] Nhắc tài liệu mỗi 30 phút → kênh %s",
            DOCUMENT_REMINDER_CHANNEL_ID,
        )

    async def cog_unload(self) -> None:
        self.doc_reminder_loop.cancel()

    @tasks.loop(minutes=30)
    async def doc_reminder_loop(self) -> None:
        channel = await _resolve_messageable(self.bot, DOCUMENT_REMINDER_CHANNEL_ID)
        if channel is None:
            return
        try:
            await channel.send(embed=_reminder_embed())
        except discord.HTTPException as e:
            logger.error(
                "[daily4] Gửi nhắc tài liệu thất bại (kênh %s): %s",
                DOCUMENT_REMINDER_CHANNEL_ID,
                e,
            )

    @doc_reminder_loop.before_loop
    async def before_doc_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()

    @commands.command(name="test12")
    async def test12(self, ctx: commands.Context) -> None:
        """Gửi bản xem trước cùng embed với tin nhắn định kỳ (kiểm tra giao diện)."""
        await ctx.send(embed=_reminder_embed())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Daily4(bot))
