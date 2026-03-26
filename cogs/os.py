import discord
from discord.ext import commands
import platform
import psutil
import os
import time
import datetime
import subprocess


# ── ID được phép dùng lệnh sysinfo ──
ALLOWED_IDS = {
    852796371622690856, 808170665969582110
}


def progress_bar(percent: float, length: int = 10) -> str:
    """Tạo thanh progress bar đẹp mắt"""
    filled = int(length * percent / 100)
    empty = length - filled
    bar = "█" * filled + "░" * empty
    # Đổi màu emoji theo mức sử dụng
    if percent < 50:
        indicator = "🟢"
    elif percent < 80:
        indicator = "🟡"
    else:
        indicator = "🔴"
    return f"{indicator} `{bar}` **{percent:.1f}%**"


def get_gpu_info() -> list[dict]:
    """Lấy thông tin GPU/VGA qua WMIC"""
    gpus = []
    try:
        # Lấy tên GPU
        name_raw = subprocess.check_output(
            "wmic path win32_videocontroller get Name /value",
            shell=True, encoding="utf-8", stderr=subprocess.DEVNULL
        )
        # Lấy VRAM
        vram_raw = subprocess.check_output(
            "wmic path win32_videocontroller get AdapterRAM /value",
            shell=True, encoding="utf-8", stderr=subprocess.DEVNULL
        )
        # Lấy driver version
        driver_raw = subprocess.check_output(
            "wmic path win32_videocontroller get DriverVersion /value",
            shell=True, encoding="utf-8", stderr=subprocess.DEVNULL
        )
        # Lấy trạng thái
        status_raw = subprocess.check_output(
            "wmic path win32_videocontroller get Status /value",
            shell=True, encoding="utf-8", stderr=subprocess.DEVNULL
        )

        names = [l.strip().replace("Name=", "") for l in name_raw.strip().splitlines() if "Name=" in l]
        vrams = [l.strip().replace("AdapterRAM=", "") for l in vram_raw.strip().splitlines() if "AdapterRAM=" in l]
        drivers = [l.strip().replace("DriverVersion=", "") for l in driver_raw.strip().splitlines() if "DriverVersion=" in l]
        statuses = [l.strip().replace("Status=", "") for l in status_raw.strip().splitlines() if "Status=" in l]

        for i, name in enumerate(names):
            if not name:
                continue
            vram_bytes = int(vrams[i]) if i < len(vrams) and vrams[i].isdigit() else 0
            vram_gb = vram_bytes / (1024 ** 3)
            vram_mb = vram_bytes / (1024 ** 2)
            vram_str = f"{vram_gb:.1f} GB" if vram_gb >= 1 else f"{vram_mb:.0f} MB"
            driver = drivers[i] if i < len(drivers) else "N/A"
            status = statuses[i] if i < len(statuses) else "N/A"
            status_emoji = "🟢" if status.lower() == "ok" else "🔴"

            gpus.append({
                "name": name,
                "vram": vram_str,
                "driver": driver,
                "status": f"{status_emoji} {status}",
            })
    except Exception:
        pass

    # Thử lấy thêm thông tin từ nvidia-smi (nếu có NVIDIA GPU)
    try:
        nv_raw = subprocess.check_output(
            "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,utilization.memory,power.draw,power.limit "
            "--format=csv,noheader,nounits",
            shell=True, encoding="utf-8", stderr=subprocess.DEVNULL, timeout=5
        )
        for i, line in enumerate(nv_raw.strip().splitlines()):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5 and i < len(gpus):
                gpus[i]["temp"] = f"{parts[0]}°C"
                gpus[i]["gpu_usage"] = f"{parts[1]}%"
                gpus[i]["mem_usage"] = f"{parts[2]}%"
                gpus[i]["power"] = f"{parts[3]}W / {parts[4]}W"
    except Exception:
        pass

    return gpus


def format_uptime(seconds: int) -> str:
    """Format uptime đẹp hơn"""
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
    """📊 Hiển thị thông tin hệ thống đang chạy bot"""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

    @commands.command(name="sysinfo", aliases=["system", "cauhinh", "vga", "gpu"])
    async def sysinfo(self, ctx):
        """Hiển thị cấu hình hệ thống đang chạy bot"""
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
                description="<a:loading:1234567890> Đang thu thập thông tin hệ thống...",
                color=discord.Color.greyple()
            )
        )

        # ═══════════════════ CPU ═══════════════════
        try:
            cpu_raw = subprocess.check_output(
                "wmic cpu get Name /value",
                shell=True, encoding="utf-8", stderr=subprocess.DEVNULL
            )
            cpu_name = cpu_raw.strip().replace("Name=", "").strip() or "Không rõ"
        except Exception:
            cpu_name = platform.processor() or "Không rõ"

        cpu_cores = psutil.cpu_count(logical=False) or 0
        cpu_threads = psutil.cpu_count(logical=True) or 0
        cpu_usage = psutil.cpu_percent(interval=1)
        cpu_freq = psutil.cpu_freq()
        cpu_freq_str = f"{cpu_freq.current:.0f} MHz ({cpu_freq.max:.0f} MHz max)" if cpu_freq else "N/A"

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

        # ═══════════════════ OS ═══════════════════
        os_name = f"{platform.system()} {platform.release()}"
        os_version = platform.version()
        os_arch = platform.machine()
        hostname = platform.node()

        # ═══════════════════ NETWORK ═══════════════════
        net = psutil.net_io_counters()
        net_sent = net.bytes_sent / (1024 ** 2)
        net_recv = net.bytes_recv / (1024 ** 2)

        # ═══════════════════ BOT PROCESS ═══════════════════
        uptime_sec = int(time.time() - self.start_time)
        process = psutil.Process(os.getpid())
        bot_ram = process.memory_info().rss / (1024 ** 2)
        bot_cpu = process.cpu_percent(interval=0.5)
        bot_threads = process.num_threads()

        # ═══════════════════ BUILD EMBED ═══════════════════
        embed = discord.Embed(
            title="",
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

        # ── CPU Field ──
        embed.add_field(
            name="<:cpu:1247930538103697478> CPU",
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

        # ── RAM Field ──
        embed.add_field(
            name="<:ram:1247930540272500826> RAM",
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

        # ── Disk Field ──
        embed.add_field(
            name="<:disk:1247930541736366132> Storage",
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

        # ── GPU/VGA Field ──
        if gpus:
            for idx, gpu in enumerate(gpus):
                gpu_lines = [
                    f"Model   : {gpu['name']}",
                    f"VRAM    : {gpu['vram']}",
                    f"Driver  : {gpu['driver']}",
                    f"Status  : {gpu['status']}",
                ]
                # Thêm thông tin chi tiết nếu có (NVIDIA)
                if "temp" in gpu:
                    gpu_lines.append(f"Temp    : {gpu['temp']}")
                if "gpu_usage" in gpu:
                    gpu_lines.append(f"GPU Use : {gpu['gpu_usage']}")
                if "mem_usage" in gpu:
                    gpu_lines.append(f"Mem Use : {gpu['mem_usage']}")
                if "power" in gpu:
                    gpu_lines.append(f"Power   : {gpu['power']}")

                gpu_title = f"🎮 GPU" if len(gpus) == 1 else f"🎮 GPU #{idx + 1}"
                gpu_value = "```yml\n" + "\n".join(gpu_lines) + "\n```"

                if "gpu_usage" in gpu:
                    try:
                        usage_val = float(gpu["gpu_usage"].replace("%", ""))
                        gpu_value += f"\n{progress_bar(usage_val)}"
                    except ValueError:
                        pass

                embed.add_field(name=gpu_title, value=gpu_value, inline=False)
        else:
            embed.add_field(
                name="🎮 GPU",
                value="```yml\nKhông tìm thấy thông tin GPU\n```",
                inline=False,
            )

        # ── OS Field ──
        embed.add_field(
            name="🖥️ Hệ điều hành",
            value=(
                f"```yml\n"
                f"OS      : {os_name}\n"
                f"Arch    : {os_arch}\n"
                f"Host    : {hostname}\n"
                f"Version : {os_version[:50]}\n"
                f"```"
            ),
            inline=True,
        )

        # ── Network Field ──
        embed.add_field(
            name="🌐 Network I/O",
            value=(
                f"```yml\n"
                f"Sent    : {net_sent:.1f} MB\n"
                f"Recv    : {net_recv:.1f} MB\n"
                f"```"
            ),
            inline=True,
        )

        # ── Bot Process Field ──
        embed.add_field(
            name="🤖 Bot Process",
            value=(
                f"```yml\n"
                f"RAM     : {bot_ram:.1f} MB\n"
                f"CPU     : {bot_cpu:.1f}%\n"
                f"Threads : {bot_threads}\n"
                f"Uptime  : {format_uptime(uptime_sec)}\n"
                f"Ping    : {round(self.bot.latency * 1000)}ms\n"
                f"Guilds  : {len(self.bot.guilds)}\n"
                f"Users   : {sum(g.member_count or 0 for g in self.bot.guilds)}\n"
                f"```"
            ),
            inline=False,
        )

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name} • Python {platform.python_version()} • discord.py {discord.__version__}",
            icon_url=ctx.author.display_avatar.url,
        )

        embed.set_thumbnail(url=self.bot.user.display_avatar.url if self.bot.user else None)

        await loading.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(SystemInfo(bot))
