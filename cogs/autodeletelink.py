import re

import discord
from discord.ext import commands

import config

# ID server áp dụng chặn link — Developer Mode: chuột phải **tên server** → Copy Server ID (không dùng ID kênh).
# Thêm số vào tuple; có thể để () nếu chỉ dùng suy guild từ ALLOWED_CHANNELS bên dưới.
AUTO_DELETE_LINK_GUILD_IDS: tuple[int, ...] = (1446866616452386856,1486759905431130175)

# True = thêm Guild ID suy ra từ các kênh trong config.ALLOWED_CHANNELS.
DERIVE_GUILDS_FROM_ALLOWED_CHANNELS = True

# http(s), www, discord invite, domain phổ biến (gồm bit.ly, youtu.be, …)
_LINK_IN_TEXT = re.compile(
    r"(?i)(?:https?://[^\s<]+|www\.[^\s<]+|discord(?:\.gg|app\.com/invite)/[^\s<]*|"
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|vn|io|gg|me|co|app|ly|be|gl|tk|ml|ga|"
    r"eu|info|biz|edu|gov|tv|cc|link|site|online|shop|dev|to|sh|ws)(?:/[^\s]*)?\b)",
)


def _message_has_link(message: discord.Message) -> bool:
    if message.content and _LINK_IN_TEXT.search(message.content):
        return True
    for emb in message.embeds:
        if emb.url:
            return True
        if emb.description and _LINK_IN_TEXT.search(emb.description):
            return True
        if emb.title and _LINK_IN_TEXT.search(emb.title):
            return True
    return False


class AutoDeleteLink(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._target_guild_ids: frozenset[int] | None = None

    async def _refresh_target_guild_ids(self) -> None:
        ids: set[int] = set(AUTO_DELETE_LINK_GUILD_IDS)
        if DERIVE_GUILDS_FROM_ALLOWED_CHANNELS:
            for cid in getattr(config, "ALLOWED_CHANNELS", []) or []:
                try:
                    ch = self.bot.get_channel(cid)
                    if ch is None:
                        ch = await self.bot.fetch_channel(cid)
                    g = getattr(ch, "guild", None)
                    if g is not None:
                        ids.add(g.id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                except Exception:
                    pass
        self._target_guild_ids = frozenset(ids)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._refresh_target_guild_ids()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.author.bot:
            return
        if self._target_guild_ids is None:
            await self._refresh_target_guild_ids()
        if not self._target_guild_ids or message.guild.id not in self._target_guild_ids:
            return
        if not _message_has_link(message):
            return
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoDeleteLink(bot))
