from discord.ext import commands
from utils.logger import setup_logger
import discord
import asyncio

logger = setup_logger(__name__)

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

async def setup(bot):
    await bot.add_cog(Chat(bot))
