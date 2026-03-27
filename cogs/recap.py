import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

_NO_MENTIONS = discord.AllowedMentions.none()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

DEFAULT_LIMIT = 40
MIN_LIMIT = 5
MAX_LIMIT = 150
MAX_TRANSCRIPT_CHARS = 28000

# Không timeout → request Groq có thể treo vô hạn, slash vẫn "đang suy nghĩ...".
_GROQ_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=90, connect=20, sock_read=75)

SUMMARY_SYSTEM_PROMPT = """Bạn tóm tắt đoạn hội thoại Discord bằng tiếng Việt.
- Ngắn gọn, rõ ràng: ý chính, quyết định, câu hỏi–trả lời quan trọng.
- Giữ tên người hoặc cách gọi trong log nếu cần để hiểu ngữ cảnh.
- Không bịa nội dung không có trong đoạn chat.
- Không cần chào hỏi hay kết luận dông dài; ưu tiên gạch đầu dòng hoặc đoạn ngắn."""


def _recap_role_ids() -> list[int]:
    raw = getattr(config, "RECAP_ALLOWED_ROLE_IDS", None)
    if raw is None:
        raw = []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _has_recap_role(member: discord.Member) -> bool:
    allowed = set(_recap_role_ids())
    if not allowed:
        return False

    owner_raw = getattr(config, "OWNER_ID", None) or ""
    try:
        if owner_raw and member.id == int(str(owner_raw).strip()):
            return True
    except (TypeError, ValueError):
        pass

    return any(role.id in allowed for role in member.roles)


async def _resolve_recap_channel(
    guild: discord.Guild, channel_id: int
) -> discord.TextChannel | discord.Thread | None:
    """Kênh thật trong cache/API — tránh proxy từ interaction khiến history/followup lạ."""
    ch = guild.get_channel(channel_id)
    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        return ch
    try:
        ch = await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.HTTPException):
        return None
    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        return ch
    return None


async def _guild_member(
    guild: discord.Guild, user: discord.abc.User
) -> discord.Member | None:
    m = guild.get_member(user.id)
    if m is not None:
        return m
    try:
        return await guild.fetch_member(user.id)
    except discord.NotFound:
        return None
    except discord.HTTPException:
        return None


def _format_history_line(msg: discord.Message) -> str | None:
    if msg.author.bot:
        return None
    text = (msg.content or "").strip()
    if not text and msg.attachments:
        text = "[đính kèm / không có chữ]"
    elif not text:
        return None
    name = msg.author.display_name
    return f"{name}: {text}"


async def _build_transcript(channel: discord.abc.Messageable, limit: int) -> str | None:
    lines: list[str] = []
    current_len = 0
    async for msg in channel.history(limit=limit, oldest_first=True):
        line = _format_history_line(msg)
        if not line:
            continue
        line_len = len(line) + 1
        if current_len + line_len > MAX_TRANSCRIPT_CHARS:
            break
        lines.append(line)
        current_len += line_len
    if not lines:
        return None
    return "\n".join(lines)


async def _groq_summarize(transcript: str) -> str | None:
    api_key = config.GROQ_API_KEY
    if not api_key:
        logger.error("[RECAP] GROQ_API_KEY chưa được cấu hình")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Hãy tóm tắt đoạn chat sau:\n\n{transcript}",
            },
        ],
        "temperature": 0.3,
        "max_tokens": 1200,
    }

    try:
        async with aiohttp.ClientSession(timeout=_GROQ_HTTP_TIMEOUT) as session:
            async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"[RECAP] Groq API {resp.status}: {err}")
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except asyncio.TimeoutError as e:
        logger.error(f"[RECAP] Groq hết thời gian chờ (timeout): {e}")
        return None
    except Exception as e:
        logger.error(f"[RECAP] Lỗi Groq: {e}")
        return None


class Recap(commands.Cog):
    """Tóm tắt đoạn chat gần đây bằng Groq (role được cấu hình trong config)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _run_recap(
        self,
        channel: discord.abc.Messageable,
        member: discord.Member | None,
        limit: int,
        respond_embed,
        respond_text,
    ) -> None:
        if not member or not _has_recap_role(member):
            await respond_text(
                "Bạn cần một trong các role được phép để dùng lệnh tóm tắt chat."
            )
            return

        transcript = await _build_transcript(channel, limit)
        if not transcript:
            await respond_text("Không có tin nhắn text gần đây để tóm tắt (bỏ qua bot và tin trống).")
            return

        summary = await _groq_summarize(transcript)
        if not summary:
            await respond_text("Không gọi được AI (kiểm tra GROQ_API_KEY hoặc thử lại sau).")
            return

        embed = discord.Embed(
            title="Tóm tắt đoạn chat",
            description=summary[:4096],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{limit} tin gần nhất (có nội dung) ")
        await respond_embed(embed)

    @app_commands.command(
        name="recap",
        description="Tóm tắt đoạn chat gần đây trong kênh này (cần role được phép)",
    )
    @app_commands.describe(soluong=f"Số tin nhắn quét (từ {MIN_LIMIT} đến {MAX_LIMIT})")
    async def recap_slash(self, interaction: discord.Interaction, soluong: int = DEFAULT_LIMIT):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Lệnh chỉ dùng trong server.", ephemeral=True
            )
            return

        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Chỉ dùng trong kênh text hoặc thread.", ephemeral=True
            )
            return

        lim = max(MIN_LIMIT, min(MAX_LIMIT, soluong))

        # Phải defer trước mọi thao tác có thể chậm (fetch_member, history, Groq),
        # nếu không Discord hết 3s và báo "Ứng dụng không phản hồi".
        await interaction.response.defer(ephemeral=True)

        member = interaction.member
        if member is None:
            member = await _guild_member(interaction.guild, interaction.user)
        if member is None:
            await interaction.edit_original_response(
                content=(
                    "Không lấy được thông tin thành viên (role). Thử lại sau hoặc kiểm tra bot có quyền xem thành viên."
                ),
                embed=None,
                allowed_mentions=_NO_MENTIONS,
            )
            return

        cid = interaction.channel_id
        if cid is None:
            await interaction.edit_original_response(
                content="Không xác định được kênh.",
                embed=None,
                allowed_mentions=_NO_MENTIONS,
            )
            return

        recap_channel = await _resolve_recap_channel(interaction.guild, cid)
        if recap_channel is None:
            await interaction.edit_original_response(
                content="Không mở được kênh text/thread để đọc lịch sử.",
                embed=None,
                allowed_mentions=_NO_MENTIONS,
            )
            return

        # Dùng edit_original_response thay vì followup: thay tin "đang suy nghĩ" trực tiếp;
        # followup webhook đôi khi treo/lỗi trong khi prefix (!recap) vẫn chạy bình thường.
        async def respond_embed(e: discord.Embed):
            await interaction.edit_original_response(
                content=None,
                embed=e,
                allowed_mentions=_NO_MENTIONS,
            )

        async def respond_text(t: str):
            await interaction.edit_original_response(
                content=t,
                embed=None,
                allowed_mentions=_NO_MENTIONS,
            )

        try:
            await self._run_recap(
                recap_channel, member, lim, respond_embed, respond_text
            )
        except Exception:
            logger.exception("[RECAP] Lỗi không mong đợi (slash)")
            try:
                await interaction.edit_original_response(
                    content="Đã xảy ra lỗi khi tóm tắt. Thử lại sau hoặc kiểm tra log bot.",
                    embed=None,
                    allowed_mentions=_NO_MENTIONS,
                )
            except discord.HTTPException:
                try:
                    await interaction.followup.send(
                        "Đã xảy ra lỗi khi tóm tắt. Thử lại sau hoặc kiểm tra log bot.",
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    pass

    @commands.command(name="recap", aliases=["tomtatchat"])
    async def recap_prefix(self, ctx: commands.Context, soluong: int = DEFAULT_LIMIT):
        """!recap [số_tin] — Tóm tắt chat gần đây (cần role được phép)."""
        if ctx.guild is None:
            await ctx.send("Lệnh chỉ dùng trong server.")
            return

        if not isinstance(ctx.channel, (discord.TextChannel, discord.Thread)):
            await ctx.send("Chỉ dùng trong kênh text hoặc thread.")
            return

        member = await _guild_member(ctx.guild, ctx.author)
        if member is None:
            await ctx.send("Không lấy được thông tin thành viên (role). Thử lại sau.")
            return

        lim = max(MIN_LIMIT, min(MAX_LIMIT, soluong))

        async def respond_embed(e: discord.Embed):
            await ctx.send(embed=e, allowed_mentions=_NO_MENTIONS)

        async def respond_text(t: str):
            await ctx.send(t, allowed_mentions=_NO_MENTIONS)

        async with ctx.channel.typing():
            await self._run_recap(ctx.channel, member, lim, respond_embed, respond_text)


async def setup(bot: commands.Bot):
    await bot.add_cog(Recap(bot))
    logger.info("[Recap] Cog loaded")
