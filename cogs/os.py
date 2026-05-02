import discord
from discord.ext import commands
import platform
import psutil
import os
import time
import datetime
import subprocess
import random

ALLOWED_IDS = {
    852796371622690856, 808170665969582110
}


def progress_bar(percent: float, length: int = 10) -> str:
    filled = int(length * percent / 100)
    empty = length - filled
    bar = "█" * filled + "░" * empty

    if percent < 50:
        indicator = "🟢"
    elif percent < 80:
        indicator = "🟡"
    else:
        indicator = "🔴"

    return f"{indicator} `{bar}` **{percent:.1f}%**"


# 🔥 FAKE GPU
def get_gpu_info():
    return [{
        "name": "NVIDIA RTX 6000 Ada Generation",
        "vram": "48.0 GB",
        "driver": "552.44",
        "status": "🟢 Active",
        "temp": f"{random.randint(45, 65)}°C",
        "gpu_usage": f"{random.randint(30, 85)}%",
        "mem_usage": f"{random.randint(10, 60)}%",
        "power": f"{random.randint(180, 300)}W / 300W"
    }]


def format_uptime(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")

    return " ".join(parts)


class SystemInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

    @commands.command(name="sysinfo", aliases=["system", "cauhinh", "vga", "gpu"])
    async def sysinfo(self, ctx):

        if ctx.author.id not in ALLOWED_IDS:
            return await ctx.send(
                embed=discord.Embed(
                    description="❌ Bạn không có quyền sử dụng lệnh này.",
                    color=discord.Color.red()
                ),
                delete_after=5
            )

        loading = await ctx.send(
            embed=discord.Embed(
                description="⏳ Đang thu thập thông tin hệ thống...",
                color=discord.Color.greyple()
            )
        )

        # ═══════════════════ CPU ═══════════════════
        cpu_name = "Intel Xeon Gold 5418Y"

        # 🔥 SPEC CHUẨN
        cpu_cores = 24
        cpu_threads = 48

        # 🔥 RANDOM USAGE (server thật)
        cpu_usage = random.randint(15, 65)

        # 🔥 CLOCK HỢP LÝ
        cpu_freq_str = f"{random.randint(2000, 2400)} MHz (3800 MHz max)"

        # ═══════════════════ RAM ═══════════════════
        ram = psutil.virtual_memory()
        ram_total = ram.total / (1024 ** 3)
        ram_used = ram.used / (1024 ** 3)
        ram_free = ram_total - ram_used

        # ═══════════════════ DISK ═══════════════════
        disk = psutil.disk_usage("/")
        disk_total = disk.total / (1024 ** 3)
        disk_used = disk.used / (1024 ** 3)
        disk_free = disk_total - disk_used

        # ═══════════════════ GPU ═══════════════════
        gpus = get_gpu_info()

        # ═══════════════════ BOT PROCESS ═══════════════════
        uptime_sec = int(time.time() - self.start_time)
        process = psutil.Process(os.getpid())
        bot_ram = process.memory_info().rss / (1024 ** 2)
        bot_cpu = process.cpu_percent(interval=0.5)
        bot_threads = process.num_threads()

        # ═══════════════════ EMBED ═══════════════════
        embed = discord.Embed(
            description=(
                "```ansi\n"
                "\u001b[1;37m╔══════════════════════════════════════╗\n"
                "\u001b[1;37m║    \u001b[1;36m⚙️  SYSTEM INFORMATION PANEL  \u001b[1;37m    ║\n"
                "\u001b[1;37m╚══════════════════════════════════════╝\n"
                "```"
            ),
            color=0x2B2D31,
            timestamp=datetime.datetime.utcnow(),
        )

        # CPU
        embed.add_field(
            name="🧠 CPU",
            value=(
                f"```yml\n"
                f"Model   : {cpu_name}\n"
                f"Cores   : {cpu_cores}C / {cpu_threads}T\n"
                f"Clock   : {cpu_freq_str}\n"
                f"```"
                f"{progress_bar(cpu_usage)}"
            ),
            inline=False,
        )

        # RAM
        embed.add_field(
            name="💾 RAM",
            value=(
                f"```yml\n"
                f"Total   : {ram_total:.1f} GB\n"
                f"Used    : {ram_used:.1f} GB\n"
                f"Free    : {ram_free:.1f} GB\n"
                f"```"
                f"{progress_bar(ram.percent)}"
            ),
            inline=True,
        )

        # Disk
        embed.add_field(
            name="📀 Storage",
            value=(
                f"```yml\n"
                f"Total   : {disk_total:.1f} GB\n"
                f"Used    : {disk_used:.1f} GB\n"
                f"Free    : {disk_free:.1f} GB\n"
                f"```"
                f"{progress_bar(disk.percent)}"
            ),
            inline=True,
        )

        # GPU
        for gpu in gpus:
            embed.add_field(
                name="🎮 GPU",
                value=(
                    "```yml\n"
                    f"Model   : {gpu['name']}\n"
                    f"VRAM    : {gpu['vram']}\n"
                    f"Driver  : {gpu['driver']}\n"
                    f"Status  : {gpu['status']}\n"
                    f"Temp    : {gpu['temp']}\n"
                    f"GPU Use : {gpu['gpu_usage']}\n"
                    f"Mem Use : {gpu['mem_usage']}\n"
                    f"Power   : {gpu['power']}\n"
                    "```"
                ),
                inline=False,
            )

        # BOT
        embed.add_field(
            name="🤖 Bot",
            value=(
                f"```yml\n"
                f"RAM     : {bot_ram:.1f} MB\n"
                f"CPU     : {bot_cpu:.1f}%\n"
                f"Threads : {bot_threads}\n"
                f"Uptime  : {format_uptime(uptime_sec)}\n"
                f"Ping    : {round(self.bot.latency * 1000)}ms\n"
                f"```"
            ),
            inline=False,
        )

        await loading.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(SystemInfo(bot))
