import discord
from discord.ext import commands
from discord.ui import Select, View

class ConsultationMenu(View):
    def __init__(self):
        super().__init__(timeout=180)
        
        # Dictionary chứa link các kênh tư vấn (thay thế bằng link kênh thực tế của bạn)
        self.channel_links = {
            "Tư vấn hướng nghiệp by long": "https://discord.com/channels/1184348724999225355/1442515721111212052",
            "Tư vấn  hướng nghiệp by bean (không nên hỏi ở đây)": "https://discord.com/channels/1184348724999225355/1444322395803488307",
            "Tư vấn Tiếng Anh": "https://discord.com/channels/1184348724999225355/1403776548145729566",
            "Tư vấn định hướng nghề nghiệp": "https://discord.com/channels/YOUR_SERVER_ID/YOUR_CHANNEL_ID",
            "Tư vấn khác": "https://discord.com/channels/YOUR_SERVER_ID/YOUR_CHANNEL_ID",
        }
        
        # Tạo Select Menu
        select = Select(
            placeholder="Chọn kênh tư vấn...",
            options=[
                discord.SelectOption(
                    label="Tư vấn hướng nghiệp by long",
                    description="Hỗ trợ các vấn đề bởi long",
                    emoji="📚"
                ),
                discord.SelectOption(
                    label="Tư vấn  hướng nghiệp by bean (không nên hỏi ở đây)",
                    description="Hỗ trợ các vấn đề kỹ thuật",
                    emoji="💻"
                ),
                discord.SelectOption(
                    label="Tư vấn Tiếng Anh",
                    description="Hỗ trợ các vấn đề liên quan đến tiếng Anh",
                    emoji="💭"
                ),
                discord.SelectOption(
                    label="Tư vấn định hướng nghề nghiệp",
                    description="Hỗ trợ định hướng nghề nghiệp",
                    emoji="💼"
                ),
                discord.SelectOption(
                    label="Tư vấn khác",
                    description="Các vấn đề khác",
                    emoji="❓"
                ),













            ]
        )
        select.callback = self.select_callback
        self.add_item(select)
    




    async def select_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        
        # Lấy link kênh từ dictionary
        channel_link = self.channel_links.get(selected)
        
        # Tạo embed thông báo với link kênh
        embed = discord.Embed(
            title=f"✅ Đã chọn: {selected}",
            description=f"Cảm ơn bạn đã chọn **{selected}**.\n\n"
                       f"📍 Vui lòng vào kênh tư vấn:\n{channel_link}\n\n"
                       f"Nhân viên tư vấn sẽ hỗ trợ bạn tại đây!",
            color=discord.Color.green()
        )
        embed.set_footer(text="Nhấn vào link để di chuyển đến kênh tư vấn")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)





























class Noti1(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="tuvan", aliases=["tv"])
    async def tuvan(self, ctx):
        """Hiển thị menu tư vấn"""
        embed = discord.Embed(
            title="🎯 Menu Tư Vấn",
            description="Vui lòng chọn loại tư vấn bạn cần hỗ trợ:",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Chọn một tùy chọn từ menu bên dưới")
        
        view = ConsultationMenu()
        await ctx.send(embed=embed, view=view)
    
    @discord.app_commands.command(name="tuvan", description="Hiển thị menu tư vấn")
    async def tuvan_slash(self, interaction: discord.Interaction):
        """Slash command để hiển thị menu tư vấn"""
        embed = discord.Embed(
            title="🎯 Menu Tư Vấn",
            description="Vui lòng chọn loại tư vấn bạn cần hỗ trợ:",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Chọn một tùy chọn từ menu bên dưới")
        
        view = ConsultationMenu()
        await interaction.response.send_message(embed=embed, view=view)
    
    @commands.Cog.listener()
    async def on_message(self, message):
        # Tránh bot tự reply chính nó
        if message.author.bot:
            return
        
        # Kiểm tra nếu tin nhắn chứa "tư vấn"
        if "11111111111tư vấn" in message.content.lower():
            embed = discord.Embed(
                title="🎯 Menu Tư Vấn",
                description="Vui lòng chọn loại tư vấn bạn cần hỗ trợ:",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Chọn một tùy chọn từ menu bên dưới")
            
            view = ConsultationMenu()
            await message.channel.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(Noti1(bot))