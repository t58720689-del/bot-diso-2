from discord.ext import commands
from utils.helpers import is_allowed_channel

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @is_allowed_channel()
    async def hello(self, ctx):
        await ctx.send("Hello!")

async def setup(bot):
    await bot.add_cog(Fun(bot))
