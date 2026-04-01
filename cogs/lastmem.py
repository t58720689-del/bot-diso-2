import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timedelta, timezone
import asyncio

class LastMem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.target_channel_id = 1446866616452386856  # Kênh cần theo dõi
        self.last_message_user = None
        self.last_message_time = None
        self.tz_vn = timezone(timedelta(hours=7))  # Múi giờ Việt Nam UTC+7
        self.check_end_of_day.start()
    
    def cog_unload(self):
        self.check_end_of_day.cancel()
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Bỏ qua tin nhắn từ bot
        if message.author.bot:
            return
        
        # Kiểm tra xem có phải kênh cần theo dõi không
        if message.channel.id == self.target_channel_id:
            self.last_message_user = message.author
            self.last_message_time = datetime.now(self.tz_vn)
    
    @tasks.loop(time=time(hour=4, minute=59, second=59))  # Chạy vào 01:00:00 mỗi ngày
    async def check_end_of_day(self):
        """Kiểm tra và thông báo người chat cuối cùng trong ngày"""
        if self.last_message_user is None:
            return
        
        channel = self.bot.get_channel(self.target_channel_id)
        if channel:
            await channel.send(
                f"🏆 Người chat cuối cùng trong ngày hôm nay là {self.last_message_user.mention}!"
            )
            # Reset cho ngày mới
            self.last_message_user = None
            self.last_message_time = None
    
    @check_end_of_day.before_loop
    async def before_check(self):
        """Đợi bot sẵn sàng trước khi bắt đầu task"""
        await self.bot.wait_until_ready()
    
    @commands.command(name="testlast")
    @commands.is_owner()
    async def test_last_message(self, ctx: commands.Context):
        """Test command để kiểm tra người chat cuối cùng"""
        if self.last_message_user is None:
            await ctx.send("❌ Chưa có ai chat trong kênh được theo dõi hôm nay!")
            return
        
        time_str = self.last_message_time.strftime("%H:%M:%S %d/%m/%Y")
        await ctx.send(
            f"📊 **Thông tin người chat cuối:**\n"
            f"👤 Người dùng: {self.last_message_user.mention}\n"
            f"⏰ Thời gian: {time_str} (UTC+7)\n"
            f"📍 Kênh: <#{self.target_channel_id}>"
        )
    
    @commands.command(name="forcechecklast")
    @commands.is_owner()
    async def force_check(self, ctx: commands.Context):
        """Test command để force thông báo người chat cuối ngay lập tức"""
        if self.last_message_user is None:
            await ctx.send("❌ Chưa có ai chat trong kênh được theo dõi hôm nay!")
            return
        
        channel = self.bot.get_channel(self.target_channel_id)
        if channel:
            await channel.send(
                f"🏆 Người chat cuối cùng trong ngày hôm nay là {self.last_message_user.mention}!"
            )
            await ctx.send("✅ Đã gửi thông báo test!")
        else:
            await ctx.send("❌ Không tìm thấy kênh!")

async def setup(bot):
    await bot.add_cog(LastMem(bot))
