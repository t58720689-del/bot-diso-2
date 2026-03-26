import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
from utils.logger import setup_logger

logger = setup_logger(__name__)


class SuKien(commands.Cog):
    """Cog quản lý và hiển thị các sự kiện trong server"""
    
    def __init__(self, bot):
        self.bot = bot

    def _format_event_status(self, status: discord.EventStatus) -> str:
        """Chuyển đổi trạng thái sự kiện sang tiếng Việt"""
        status_map = {
            discord.EventStatus.scheduled: "📅 Đã lên lịch",
            discord.EventStatus.active: "🔴 Đang diễn ra",
            discord.EventStatus.completed: "✅ Đã kết thúc",
            discord.EventStatus.cancelled: "❌ Đã hủy",
        }
        return status_map.get(status, "❓ Không xác định")

    def _format_location(self, event: discord.ScheduledEvent) -> str:
        """Lấy thông tin địa điểm của sự kiện"""
        if event.entity_type == discord.EntityType.voice:
            return f"🔊 Voice: {event.channel.mention if event.channel else 'N/A'}"
        elif event.entity_type == discord.EntityType.stage_instance:
            return f"🎭 Stage: {event.channel.mention if event.channel else 'N/A'}"
        elif event.entity_type == discord.EntityType.external:
            return f"🌐 Bên ngoài: {event.location or 'N/A'}"
        return "📍 Không xác định"

    def _format_time(self, dt: datetime) -> str:
        """Format thời gian sang định dạng Việt Nam (GMT+7)"""
        if dt is None:
            return "Chưa xác định"
        # Chuyển sang GMT+7
        vn_tz = timezone(timedelta(hours=7))
        dt_vn = dt.astimezone(vn_tz)
        return dt_vn.strftime("%H:%M %d/%m/%Y")

    def _get_time_until(self, dt: datetime) -> str:
        """Tính thời gian còn lại đến sự kiện"""
        if dt is None:
            return ""
        now = datetime.now(timezone.utc)
        diff = dt - now
        
        if diff.total_seconds() < 0:
            return "Đã bắt đầu"
        
        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days} ngày")
        if hours > 0:
            parts.append(f"{hours} giờ")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes} phút")
        
        return f"⏰ Còn {' '.join(parts)}" if parts else "⏰ Sắp bắt đầu"

    @app_commands.command(name="sukien", description="Hiển thị danh sách các sự kiện trong server")
    @app_commands.describe(
        loai="Lọc theo loại sự kiện"
    )
    @app_commands.choices(loai=[
        app_commands.Choice(name="Tất cả", value="all"),
        app_commands.Choice(name="Đang diễn ra", value="active"),
        app_commands.Choice(name="Sắp diễn ra", value="scheduled"),
    ])
    async def sukien(self, interaction: discord.Interaction, loai: str = "all"):
        """Hiển thị các sự kiện đang mở trong server"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Lấy tất cả scheduled events từ server
            events = await interaction.guild.fetch_scheduled_events()
            
            if not events:
                embed = discord.Embed(
                    title="📅 Sự Kiện Server",
                    description="*Hiện tại không có sự kiện nào được lên lịch.*",
                    color=discord.Color.orange()
                )
                embed.set_footer(text=f"Server: {interaction.guild.name}")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Lọc sự kiện theo loại
            if loai == "active":
                events = [e for e in events if e.status == discord.EventStatus.active]
            elif loai == "scheduled":
                events = [e for e in events if e.status == discord.EventStatus.scheduled]
            else:
                # Hiển thị cả active và scheduled, loại bỏ completed và cancelled
                events = [e for e in events if e.status in [
                    discord.EventStatus.active, 
                    discord.EventStatus.scheduled
                ]]
            
            if not events:
                filter_text = {
                    "active": "đang diễn ra",
                    "scheduled": "sắp diễn ra",
                    "all": ""
                }
                embed = discord.Embed(
                    title="📅 Sự Kiện Server",
                    description=f"*Không có sự kiện {filter_text.get(loai, '')} nào.*",
                    color=discord.Color.orange()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Sắp xếp sự kiện theo thời gian bắt đầu
            events = sorted(events, key=lambda e: e.start_time or datetime.max.replace(tzinfo=timezone.utc))
            
            # Tạo embed chính
            embed = discord.Embed(
                title=f"📅 Sự Kiện Server - {interaction.guild.name}",
                description=f"Tìm thấy **{len(events)}** sự kiện",
                color=discord.Color.blue()
            )
            
            for i, event in enumerate(events[:10], 1):  # Giới hạn 10 sự kiện
                # Tạo nội dung cho mỗi sự kiện
                status = self._format_event_status(event.status)
                location = self._format_location(event)
                start_time = self._format_time(event.start_time)
                end_time = self._format_time(event.end_time) if event.end_time else "Không xác định"
                time_until = self._get_time_until(event.start_time) if event.status == discord.EventStatus.scheduled else ""
                
                # Đếm số người quan tâm
                interested = event.user_count or 0
                
                field_value = (
                    f"{status}\n"
                    f"{location}\n"
                    f"🕐 **Bắt đầu:** {start_time}\n"
                    f"🕑 **Kết thúc:** {end_time}\n"
                    f"👥 **Quan tâm:** {interested} người"
                )
                
                if time_until:
                    field_value += f"\n{time_until}"
                
                if event.description:
                    # Cắt ngắn mô tả nếu quá dài
                    desc = event.description[:100] + "..." if len(event.description) > 100 else event.description
                    field_value += f"\n📝 {desc}"
                
                embed.add_field(
                    name=f"{i}. {event.name}",
                    value=field_value,
                    inline=False
                )
            
            if len(events) > 10:
                embed.set_footer(text=f"Hiển thị 10/{len(events)} sự kiện • Server: {interaction.guild.name}")
            else:
                embed.set_footer(text=f"Server: {interaction.guild.name}")
            
            # Thêm thumbnail nếu server có icon
            if interaction.guild.icon:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user} đã xem danh sách sự kiện trong {interaction.guild.name}")
            
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Lỗi Quyền Truy Cập",
                description="Bot không có quyền xem các sự kiện trong server này.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Lỗi khi lấy danh sách sự kiện: {e}")
            embed = discord.Embed(
                title="❌ Đã Xảy Ra Lỗi",
                description="Không thể lấy danh sách sự kiện. Vui lòng thử lại sau.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="sukien_chitiet", description="Xem chi tiết một sự kiện cụ thể")
    @app_commands.describe(ten_sukien="Tên của sự kiện muốn xem chi tiết")
    async def sukien_chitiet(self, interaction: discord.Interaction, ten_sukien: str):
        """Xem chi tiết một sự kiện cụ thể"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            events = await interaction.guild.fetch_scheduled_events()
            
            # Tìm sự kiện theo tên (không phân biệt hoa thường)
            event = None
            for e in events:
                if ten_sukien.lower() in e.name.lower():
                    event = e
                    break
            
            if not event:
                embed = discord.Embed(
                    title="❌ Không Tìm Thấy",
                    description=f"Không tìm thấy sự kiện nào với tên **{ten_sukien}**",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Tạo embed chi tiết
            embed = discord.Embed(
                title=f"📅 {event.name}",
                description=event.description or "*Không có mô tả*",
                color=discord.Color.blue()
            )
            
            # Thông tin cơ bản
            embed.add_field(
                name="📊 Trạng thái",
                value=self._format_event_status(event.status),
                inline=True
            )
            
            embed.add_field(
                name="📍 Địa điểm",
                value=self._format_location(event),
                inline=True
            )
            
            embed.add_field(
                name="👥 Người quan tâm",
                value=f"{event.user_count or 0} người",
                inline=True
            )
            
            # Thời gian
            embed.add_field(
                name="🕐 Bắt đầu",
                value=self._format_time(event.start_time),
                inline=True
            )
            
            embed.add_field(
                name="🕑 Kết thúc",
                value=self._format_time(event.end_time) if event.end_time else "Không xác định",
                inline=True
            )
            
            # Thời gian còn lại
            if event.status == discord.EventStatus.scheduled:
                embed.add_field(
                    name="⏰ Còn lại",
                    value=self._get_time_until(event.start_time),
                    inline=True
                )
            
            # Người tạo
            if event.creator:
                embed.add_field(
                    name="👤 Người tạo",
                    value=event.creator.mention,
                    inline=True
                )
            
            # Hình ảnh sự kiện
            if event.cover_image:
                embed.set_image(url=event.cover_image.url)
            
            embed.set_footer(text=f"ID: {event.id} • Server: {interaction.guild.name}")
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user} đã xem chi tiết sự kiện: {event.name}")
            
        except Exception as e:
            logger.error(f"Lỗi khi lấy chi tiết sự kiện: {e}")
            embed = discord.Embed(
                title="❌ Đã Xảy Ra Lỗi",
                description="Không thể lấy chi tiết sự kiện. Vui lòng thử lại sau.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(SuKien(bot))
    logger.info("SuKien cog loaded successfully")
