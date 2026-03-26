# Helper functions
import discord
from discord.ext import commands
import config

def is_allowed_channel():
    """
    Decorator để kiểm tra xem lệnh có được sử dụng trong kênh được phép không.
    Nếu danh sách ALLOWED_CHANNELS rỗng, cho phép tất cả các kênh.
    """
    async def predicate(ctx):
        # Nếu không có kênh nào trong danh sách, cho phép tất cả
        if not config.ALLOWED_CHANNELS:
            return True
        
        # Kiểm tra xem channel hiện tại có trong danh sách cho phép không
        if ctx.channel.id in config.ALLOWED_CHANNELS:
            return True
        
        # Thông báo nếu kênh không được phép
        await ctx.send(f"⚠️ Bot chỉ hoạt động trong các kênh được chỉ định. Kênh này không được phép sử dụng bot.", delete_after=2)
        return False
    
    return commands.check(predicate)

def is_allowed_channel_for_message(channel_id):
    """
    Kiểm tra xem một channel_id có được phép không (dùng cho on_message listener).
    Trả về True nếu channel được phép, False nếu không.
    """
    # Nếu không có kênh nào trong danh sách, cho phép tất cả
    if not config.ALLOWED_CHANNELS:
        return True
    
    return channel_id in config.ALLOWED_CHANNELS

