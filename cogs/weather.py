import discord
from discord.ext import commands
import aiohttp
import config
from datetime import datetime, timezone
from utils.logger import setup_logger

logger = setup_logger(__name__)

class Weather(commands.Cog):
    """Tính năng thời tiết sử dụng OpenWeatherMap API"""
    
    def __init__(self, bot):
        self.bot = bot
        self.api_key = config.OPENWEATHER_API_KEY
        self.current_url = "https://api.openweathermap.org/data/2.5/weather"
        self.forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
    
    async def get_current_weather(self, city: str):
        """Lấy thời tiết hiện tại"""
        async with aiohttp.ClientSession() as session:
            params = {
                'q': city,
                'appid': self.api_key,
                'units': 'metric',
                'lang': 'vi'
            }
            async with session.get(self.current_url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"API Error: {response.status}")
                    return None
    
    async def get_forecast_data(self, city: str):
        """Lấy dự báo 5 ngày"""
        async with aiohttp.ClientSession() as session:
            params = {
                'q': city,
                'appid': self.api_key,
                'units': 'metric',
                'lang': 'vi'
            }
            async with session.get(self.forecast_url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"API Error: {response.status}")
                    return None
    
    def get_weather_emoji(self, weather_id: int) -> str:
        """Lấy emoji phù hợp với mã thời tiết"""
        if 200 <= weather_id < 300:  # Thunderstorm
            return "⛈️"
        elif 300 <= weather_id < 400:  # Drizzle
            return "🌧️"
        elif 500 <= weather_id < 600:  # Rain
            return "🌧️"
        elif 600 <= weather_id < 700:  # Snow
            return "❄️"
        elif 700 <= weather_id < 800:  # Atmosphere (fog, etc)
            return "🌫️"
        elif weather_id == 800:  # Clear
            return "☀️"
        elif weather_id == 801:  # Few clouds
            return "🌤️"
        elif weather_id == 802:  # Scattered clouds
            return "⛅"
        elif 803 <= weather_id < 900:  # Clouds
            return "☁️"
        return "🌡️"
    
    def format_timestamp(self, timestamp: int) -> str:
        """Chuyển đổi timestamp thành chuỗi thời gian"""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime("%H:%M")
    
    @commands.command(name="weather", aliases=['thoitiet'])
    async def weather(self, ctx, *, city: str):
        """Hiển thị thời tiết hiện tại của một thành phố
        Cách dùng: !weather <tên thành phố>
        Ví dụ: !weather Hanoi"""
        
        # Lấy dữ liệu thời tiết
        weather_data = await self.get_current_weather(city)
        if not weather_data:
            await ctx.send(f"❌ Không tìm thấy thành phố: {city}")
            return
        
        current = weather_data
        weather = current['weather'][0]
        city_name = current['name']
        
        # Tạo embed
        embed = discord.Embed(
            title=f"🌤️ Thời tiết tại {city_name}",
            description=f"**{weather['description'].capitalize()}**",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        emoji = self.get_weather_emoji(weather['id'])
        
        # Thêm thông tin
        main = current['main']
        embed.add_field(
            name="🌡️ Nhiệt độ",
            value=f"**{main['temp']:.1f}°C**\nCảm giác: {main['feels_like']:.1f}°C",
            inline=True
        )
        
        embed.add_field(
            name="💧 Độ ẩm",
            value=f"{main['humidity']}%",
            inline=True
        )
        
        embed.add_field(
            name="💨 Gió",
            value=f"{current['wind']['speed']:.1f} m/s",
            inline=True
        )
        
        embed.add_field(
            name="☁️ Mây",
            value=f"{current['clouds']['all']}%",
            inline=True
        )
        
        embed.add_field(
            name="👁️ Tầm nhìn",
            value=f"{current.get('visibility', 0) / 1000:.1f} km",
            inline=True
        )
        
        sys_data = current['sys']
        embed.add_field(
            name="🌅 Mặt trời",
            value=f"↑ {self.format_timestamp(sys_data['sunrise'])}\n↓ {self.format_timestamp(sys_data['sunset'])}",
            inline=True
        )
        
        if 'rain' in current:
            embed.add_field(
                name="🌧️ Mưa (1h)",
                value=f"{current['rain'].get('1h', 0):.1f} mm",
                inline=True
            )
        
        embed.set_thumbnail(url=f"http://openweathermap.org/img/wn/{weather['icon']}@2x.png")
        embed.set_footer(text=f"Yêu cầu bởi {ctx.author.name}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="forecast", aliases=['dubao'])
    async def forecast(self, ctx, *, city: str):
        """Hiển thị dự báo thời tiết 5 ngày
        Cách dùng: !forecast <tên thành phố>
        Ví dụ: !forecast Hanoi"""
        
        # Lấy dữ liệu dự báo
        weather_data = await self.get_forecast_data(city)
        if not weather_data:
            await ctx.send(f"❌ Không tìm thấy thành phố: {city}")
            return
        
        city_name = weather_data['city']['name']
        
        # Tạo embed
        embed = discord.Embed(
            title=f"📅 Dự báo 5 ngày - {city_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Nhóm dự báo theo ngày (lấy dự báo 12:00 mỗi ngày)
        daily_forecasts = {}
        for item in weather_data['list']:
            date = datetime.fromtimestamp(item['dt'], tz=timezone.utc)
            date_str = date.strftime('%Y-%m-%d')
            
            # Lấy dự báo buổi trưa (12:00) hoặc gần nhất
            if date_str not in daily_forecasts:
                daily_forecasts[date_str] = item
            elif '12:00' in date.strftime('%H:%M'):
                daily_forecasts[date_str] = item
        
        # Hiển thị 5 ngày đầu
        for date_str, day in list(daily_forecasts.items())[:5]:
            date = datetime.fromtimestamp(day['dt'], tz=timezone.utc)
            weather = day['weather'][0]
            emoji = self.get_weather_emoji(weather['id'])
            main = day['main']
            
            value = (
                f"{emoji} {weather['description'].capitalize()}\n"
                f"🌡️ {main['temp']:.1f}°C (Cảm giác: {main['feels_like']:.1f}°C)\n"
                f"💧 {main['humidity']}% | 💨 {day['wind']['speed']:.1f} m/s"
            )
            
            if 'rain' in day:
                value += f"\n🌧️ {day['rain'].get('3h', 0):.1f} mm"
            
            embed.add_field(
                name=f"📆 {date.strftime('%d/%m/%Y - %A')}",
                value=value,
                inline=False
            )
        
        embed.set_footer(text=f"Yêu cầu bởi {ctx.author.name}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="hourly", aliases=['theogio'])
    async def hourly(self, ctx, *, city: str):
        """Hiển thị dự báo theo giờ
        Cách dùng: !hourly <tên thành phố>
        Ví dụ: !hourly Hanoi"""
        
        # Lấy dữ liệu dự báo
        weather_data = await self.get_forecast_data(city)
        if not weather_data:
            await ctx.send(f"❌ Không tìm thấy thành phố: {city}")
            return
        
        city_name = weather_data['city']['name']
        
        # Tạo embed
        embed = discord.Embed(
            title=f"⏰ Dự báo theo giờ - {city_name}",
            description="Dự báo cho 24 giờ tới (mỗi 3 giờ)",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Thêm dự báo cho 8 khoảng thời gian đầu (24 giờ)
        hourly_text = ""
        for item in weather_data['list'][:8]:
            time = datetime.fromtimestamp(item['dt'], tz=timezone.utc)
            weather = item['weather'][0]
            emoji = self.get_weather_emoji(weather['id'])
            main = item['main']
            
            hourly_text += (
                f"**{time.strftime('%d/%m %H:%M')}** {emoji} {main['temp']:.1f}°C "
                f"💧{main['humidity']}% 💨{item['wind']['speed']:.1f}m/s\n"
            )
        
        embed.description = hourly_text
        embed.set_footer(text=f"Yêu cầu bởi {ctx.author.name}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="alerts", aliases=['canhbao'])
    async def alerts(self, ctx, *, city: str):
        """Cảnh báo thời tiết (API miễn phí không hỗ trợ)
        Cách dùng: !alerts <tên thành phố>
        Ví dụ: !alerts Hanoi"""
        
        embed = discord.Embed(
            title=f"ℹ️ Thông báo",
            description="Tính năng cảnh báo thời tiết yêu cầu API trả phí.\nVui lòng sử dụng `!weather` hoặc `!forecast` để xem thời tiết.",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Yêu cầu bởi {ctx.author.name}")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Weather(bot))
    logger.info("Weather cog loaded successfully")
    logger.info("Weather cog loaded successfully")

