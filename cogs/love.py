import discord
from discord.ext import commands
from utils.helpers import is_allowed_channel
import random
import hashlib
import io
import math
import asyncio
import aiohttp
from PIL import Image, ImageDraw, ImageFont


class Love(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Tính toán ──────────────────────────────────────────────

    def calculate_love_percentage(self, user1_id: int, user2_id: int) -> int:
        ids = sorted([user1_id, user2_id])
        hash_input = f"{ids[0]}{ids[1]}".encode()
        hash_result = hashlib.md5(hash_input).hexdigest()
        return int(hash_result, 16) % 101

    def get_love_message(self, percentage: int) -> str:
        if percentage >= 90:
            return "Hoàn hảo! Đây là cặp đôi trời sinh! 💖✨"
        elif percentage >= 70:
            return "Rất tuyệt! Mối quan hệ này rất hứa hẹn! 💕"
        elif percentage >= 50:
            return "Khá tốt! Có tiềm năng phát triển! 💗"
        elif percentage >= 30:
            return "Còn nhiều thứ cần khám phá! 💝"
        elif percentage >= 10:
            return "Hơi khó khăn nhưng không phải không thể! 💔"
        else:
            return "Có vẻ không hợp lắm... nhưng ít nhất cũng vui! 💀💔"

    def get_heart_emoji(self, percentage: int) -> str:
        if percentage >= 90:
            return "💖"
        elif percentage >= 70:
            return "❤️"
        elif percentage >= 50:
            return "💕"
        elif percentage >= 30:
            return "💗"
        elif percentage >= 10:
            return "💔"
        else:
            return "💔"

    # ── Xử lý ảnh ─────────────────────────────────────────────

    async def fetch_avatar(self, user: discord.Member, size: int = 256) -> Image.Image:
        """Tải avatar của user"""
        url = user.display_avatar.with_size(size).url
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA").resize(
                        (size, size), Image.LANCZOS
                    )
        return Image.new("RGBA", (size, size), (114, 137, 218, 255))

    @staticmethod
    def _circle_avatar(avatar: Image.Image, size: int, border: int = 5,
                       border_color=(255, 255, 255)) -> Image.Image:
        """Cắt avatar thành hình tròn có viền trắng"""
        total = size + border * 2
        out = Image.new("RGBA", (total, total), (0, 0, 0, 0))
        draw = ImageDraw.Draw(out)
        draw.ellipse([0, 0, total - 1, total - 1], fill=(*border_color, 200))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
        av = avatar.resize((size, size), Image.LANCZOS)
        out.paste(av, (border, border), mask)
        return out

    @staticmethod
    def _draw_heart_outline(draw: ImageDraw.ImageDraw,
                            cx: int, cy: int, scale: float,
                            color=(255, 255, 255, 220), width: int = 3):
        """Vẽ đường viền trái tim toán học"""
        pts = []
        for deg in range(360):
            t = math.radians(deg)
            x = scale * 16 * math.sin(t) ** 3
            y = -scale * (13 * math.cos(t) - 5 * math.cos(2 * t)
                          - 2 * math.cos(3 * t) - math.cos(4 * t))
            pts.append((cx + x, cy + y))
        for i in range(len(pts)):
            draw.line([pts[i], pts[(i + 1) % len(pts)]], fill=color, width=width)

    @staticmethod
    def _gradient(w: int, h: int) -> Image.Image:
        """Tạo gradient hồng‑tím"""
        img = Image.new("RGBA", (w, h))
        d = ImageDraw.Draw(img)
        for x in range(w):
            r = int(200 + (155 - 200) * (x / w))
            g = int(100 + (70 - 100) * (x / w))
            b = int(200 + (225 - 200) * (x / w))
            d.line([(x, 0), (x, h)], fill=(r, g, b, 255))
        return img

    def create_love_image(self, av1: Image.Image, av2: Image.Image,
                          percentage: int) -> io.BytesIO:
        """Tạo love card giống ảnh mẫu: avatar ── trái tim %% ── avatar"""
        W, H = 700, 250
        AV = 160
        BORDER = 5
        RADIUS = 30

        gradient = self._gradient(W, H)
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rmask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(rmask).rounded_rectangle([0, 0, W - 1, H - 1],
                                                 radius=RADIUS, fill=255)
        card.paste(gradient, (0, 0), rmask)

        brd = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(brd).rounded_rectangle(
            [0, 0, W - 1, H - 1], radius=RADIUS,
            outline=(220, 180, 255, 200), width=4
        )
        card = Image.alpha_composite(card, brd)

        c1 = self._circle_avatar(av1, AV, BORDER)
        c2 = self._circle_avatar(av2, AV, BORDER)
        y = (H - (AV + BORDER * 2)) // 2
        card.paste(c1, (55, y), c1)
        card.paste(c2, (W - 55 - (AV + BORDER * 2), y), c2)

        draw = ImageDraw.Draw(card)
        cx, cy = W // 2, H // 2
        self._draw_heart_outline(draw, cx, cy - 5, scale=3.4,
                                 color=(255, 255, 255, 220), width=3)

        text = f"{percentage}%"
        try:
            font = ImageFont.truetype("arial.ttf", 38)
        except OSError:
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 38)
            except OSError:
                font = ImageFont.load_default()

        bb = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        tx, ty = cx - tw // 2, cy - th // 2 - 5
        draw.text((tx + 1, ty + 1), text, fill=(0, 0, 0, 100), font=font)
        draw.text((tx, ty), text, fill=(255, 255, 255), font=font)

        for sx, sy in [(cx - 80, cy - 55), (cx + 75, cy - 50),
                       (cx - 65, cy + 48), (cx + 60, cy + 42)]:
            draw.ellipse([sx - 2, sy - 2, sx + 2, sy + 2],
                         fill=(255, 255, 255, 180))

        buf = io.BytesIO()
        card.save(buf, "PNG")
        buf.seek(0)
        return buf

    # ── Helpers embed ──────────────────────────────────────────

    def build_embed(self, user1, user2, pct, heart, msg, author,
                    fname="love_card.png"):
        embed = discord.Embed(
            description=(
                f"{user1} + {user2} = **{pct}% of Love {heart}**\n"
                f"💀 **{user1.display_name}** và **{user2.display_name}** {msg}"
            ),
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_image(url=f"attachment://{fname}")
        embed.set_footer(text=f"Được yêu cầu bởi {author.display_name}",
                         icon_url=author.display_avatar.url)
        return embed

    # ── Command chính (!lovecalc) ──────────────────────────────

    LOVECALC_CHANNELS = {1474535485488631911}

    @commands.command(name="lovecalc", aliases=["love", "tinhyeu"])
    async def lovecalc(self, ctx, user1: discord.Member = None,
                       user2: discord.Member = None):
        """!lovecalc @user1 @user2 | !lovecalc @user | !lovecalc"""
        if ctx.channel.id not in self.LOVECALC_CHANNELS:
            return await ctx.message.delete()
        if user1 is None and user2 is None:
            user1 = ctx.author
            members = [m for m in ctx.guild.members if not m.bot and m.id != ctx.author.id]
            if not members:
                return await ctx.send("❌ Không tìm thấy thành viên nào khác!")
            user2 = random.choice(members)
        elif user2 is None:
            user2 = ctx.author
            if user1.id == user2.id:
                members = [m for m in ctx.guild.members if not m.bot and m.id != user1.id]
                if not members:
                    return await ctx.send("❌ Không tìm thấy thành viên nào khác!")
                user2 = random.choice(members)

        if user1.id == user2.id:
            return await ctx.send("❌ Không thể tính độ hợp với chính mình!")

        async with ctx.typing():
            pct = self.calculate_love_percentage(user1.id, user2.id)
            heart = self.get_heart_emoji(pct)
            msg = self.get_love_message(pct)

            av1 = await self.fetch_avatar(user1, 256)
            av2 = await self.fetch_avatar(user2, 256)
            buf = self.create_love_image(av1, av2, pct)

            file = discord.File(fp=buf, filename="love_card.png")
            embed = self.build_embed(user1, user2, pct, heart, msg, ctx.author)

        bot_msg = await ctx.send(
            embed=embed, file=file,
            allowed_mentions=discord.AllowedMentions.none()
        )

        # Tự xoá sau 20 giây
        await asyncio.sleep(20)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        try:
            await bot_msg.delete()
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Love(bot))
