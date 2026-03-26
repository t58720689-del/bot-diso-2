import discord
from discord.ext import commands
import config
import os
from datetime import datetime, timezone
from utils.logger import setup_logger

logger = setup_logger(__name__)

class MixiBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='!',
            intents=discord.Intents.all(),
            help_command=None
        )

    async def setup_hook(self):
        # Load cogs
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename != '__init__.py':
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f'Loaded extension: {filename}')
                except Exception as e:
                    logger.error(f'Failed to load extension {filename}: {e}')

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')

        # Guild sync tức thì cho tất cả server bot đang có mặt
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info(f'Guild sync [{guild.name}]: {len(synced)} command(s)')
            except Exception as e:
                logger.error(f'Failed to guild sync [{guild.name}]: {e}')


bot = MixiBot()


# ─── Lệnh sync & debug đặt ngoài class ───────────────────────────────
@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx: commands.Context):
    """!sync — Sync slash commands lên guild hiện tại"""
    global_cmds = bot.tree.get_commands()
    await ctx.send(f"🔍 Tree có **{len(global_cmds)}** commands: {[c.name for c in global_cmds]}")

    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ Đã sync **{len(synced)}** slash command(s) lên guild này!")


@bot.command(name="cogs")
@commands.is_owner()
async def list_cogs(ctx: commands.Context):
    """!cogs — Xem danh sách cogs đã load"""
    loaded = list(bot.cogs.keys())
    await ctx.send(f"📦 Loaded cogs ({len(loaded)}): {loaded}")


if __name__ == '__main__':
    bot.run(config.TOKEN)































