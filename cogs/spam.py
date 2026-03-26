import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import aiohttp
import discord
from discord.ext import commands, tasks

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

SPAM_THRESHOLD = 30
TIMEOUT_HOURS = 8
MAX_TEXT_FOR_API = 3500
# Quét định kỳ: mỗi N giờ lấy M tin mới nhất / kênh theo dõi (cùng logic AI như on_message)
HOURLY_SCAN_INTERVAL_HOURS = 1
HOURLY_SCAN_MESSAGE_LIMIT = 5

# Kênh có thể gửi tin (text, thread, chat trong voice)
_MESSAGEABLE_GUILD = (discord.TextChannel, discord.Thread, discord.VoiceChannel)

SYSTEM_PROMPT = """Bạn là hệ thống phát hiện tin nhắn SPAM trên Discord (tiếng Việt và tiếng Anh).

Đánh giá mức độ spam / lừa đảo / quảng cáo rác (0-100):
- 0-30: Tin bình thường, trò chuyện, hỏi đáp hợp lệ
- 31-50: Hơi giống quảng cáo nhưng có thể chấp nhận
- 51-75: Nghi ngờ spam, nhắc nhiều lần link, mời server lạ, "free nitro", kiểu nick bị hack,share 1 file nén ( rar bất thường)
- 76-100: Rõ ràng spam/scam: flood link discord.gg / discord.com/invite, "check this out", crypto/airdrop lừa đảo, clone link, yêu cầu bấm link gấp, copy-paste quảng cáo, share 1 file nén ( rar bất thường)

Lưu ý:
- Phân tích teencode, không dấu
- Tin hợp pháp có 1 link chia sẻ = điểm thấp; nhiều link mời/giật gân = điểm cao
- Không nhầm trò chuyện thường với spam

Trả lời ĐÚNG định dạng JSON (không thêm gì khác):
{"score": <số_nguyên_0_đến_100>, "reason": "<giải_thích_ngắn_gọn>"}"""


def _watch_channel_ids():
    ids = getattr(config, "SPAM_WATCH_CHANNEL_IDS", None)
    if ids is None:
        single = getattr(config, "SPAM_WATCH_CHANNEL_ID", None)
        return [single] if single is not None else []
    return list(ids)


def _channel_matches_watch(message: discord.Message, watch_ids: list) -> bool:
    """Khớp ID kênh hoặc thread con của kênh đó (forum / thread reply)."""
    if not watch_ids:
        return False
    watch_set = set(watch_ids)
    ch = message.channel
    if ch.id in watch_set:
        return True
    if isinstance(ch, discord.Thread) and ch.parent_id and ch.parent_id in watch_set:
        return True
    return False


def _should_bypass(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_messages


def _message_text_for_ai(message: discord.Message) -> str:
    """Nội dung chữ + mô tả embed (khi content rỗng hoặc scam hay dùng embed)."""
    parts = []
    if message.content and message.content.strip():
        parts.append(message.content.strip())
    for em in message.embeds:
        if em.title:
            parts.append(em.title)
        if em.description:
            parts.append(em.description)
        for f in em.fields:
            if f.name:
                parts.append(f.name)
            if f.value:
                parts.append(f.value)
        if em.url:
            parts.append(em.url)
    return "\n".join(parts).strip()


@asynccontextmanager
async def _typing_if_supported(channel: discord.abc.Messageable):
    """Thử bật typing; VoiceChannel / client lạ có thể không hỗ trợ — không làm hỏng luồng."""
    try:
        async with channel.typing():
            yield
    except (AttributeError, discord.ClientException, TypeError, NotImplementedError):
        yield


class SpamWatch(commands.Cog):
    """Kênh cấu hình: Groq chấm điểm spam; từ ngưỡng → xóa tin, timeout, thông báo."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if _watch_channel_ids():
            self.hourly_watch_scan.start()
            logger.info(
                "[SPAM] Quét định kỳ: mỗi %sh, %s tin/kênh | IDs: %s",
                HOURLY_SCAN_INTERVAL_HOURS,
                HOURLY_SCAN_MESSAGE_LIMIT,
                _watch_channel_ids(),
            )

    async def cog_unload(self):
        self.hourly_watch_scan.cancel()

    async def analyze_spam(self, text: str) -> dict | None:
        api_key = config.GROQ_API_KEY
        if not api_key:
            logger.error("[SPAM] GROQ_API_KEY chưa được cấu hình trong .env")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        safe_text = text[:MAX_TEXT_FOR_API]
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Phân tích tin nhắn sau (có thể là spam hay không):\n\n{json.dumps(safe_text)}",
                },
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }

        content = ""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[SPAM] Groq API error {resp.status}: {error_text}")
                        return None

                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"].strip()

                    if content.startswith("```"):
                        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                    return json.loads(content)

        except json.JSONDecodeError as e:
            logger.error(f"[SPAM] Không parse được JSON từ Groq: {e} | raw: {content!r}")
            return None
        except Exception as e:
            logger.error(f"[SPAM] Lỗi khi gọi Groq API: {e}")
            return None

    async def _notify_channel(
        self, channel: discord.abc.Messageable, embed: discord.Embed, delete_after: float = 12.0
    ):
        try:
            msg = await channel.send(embed=embed)
            await asyncio.sleep(delete_after)
            try:
                await msg.delete()
            except discord.NotFound:
                pass
        except discord.Forbidden:
            logger.warning("[SPAM] Không gửi được thông báo tại channel %s", channel.id)
        except Exception as e:
            logger.error("[SPAM] Lỗi thông báo: %s", e)

    async def _apply_spam_action(
        self,
        message: discord.Message,
        member: discord.Member,
        score: int,
        reason: str,
    ):
        channel = message.channel
        if not isinstance(channel, _MESSAGEABLE_GUILD):
            logger.warning("[SPAM] Bỏ qua xử lý: loại kênh không hỗ trợ %s", type(channel).__name__)
            return

        preview = _message_text_for_ai(message)[:500] or "(không có nội dung chữ)"

        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            logger.warning("[SPAM] Bot không có quyền xóa tin trong channel %s", channel.id)
            await self._notify_channel(
                channel,
                discord.Embed(
                    title="Spam (AI)",
                    description=f"Điểm **{score}/100** nhưng bot không xóa được tin. Kiểm tra quyền.\nLý do: {reason}",
                    color=discord.Color.dark_red(),
                ),
            )
            return

        logger.info("[SPAM] Đã xóa tin từ %s trong #%s — điểm %s — %s", member, channel, score, reason)

        if member.guild_permissions.administrator:
            embed = discord.Embed(
                title="Phát hiện spam (AI)",
                description=(
                    f"**Người gửi:** {member.mention}\n"
                    f"> {preview}\n\n"
                    f"Điểm spam: **{score}/100**\n"
                    f"Lý do: {reason}\n\n"
                    "⚠️ Không timeout vì người dùng là Admin."
                ),
                color=discord.Color.orange(),
            )
            await self._notify_channel(channel, embed)
            return

        try:
            await member.timeout(
                timedelta(hours=TIMEOUT_HOURS),
                reason=f"[Auto-mod spam] score {score}/100 — {reason}",
            )
            embed = discord.Embed(
                title="Đã xử lý spam ",
                description=(
                    f"**Người vi phạm:** {member.mention}\n"
                    f"**Tin đã xóa:**\n> {preview}\n\n"
                    f"Điểm spam: **{score}/100**\n"
                    f"Lý do: {reason}\n"
                    f"Timeout: **{TIMEOUT_HOURS} giờ**"
                ),
                color=discord.Color.red(),
            )
            await self._notify_channel(channel, embed)
            logger.info("[SPAM] Timeout %s %sh (score=%s)", member, TIMEOUT_HOURS, score)
        except discord.Forbidden:
            await self._notify_channel(
                channel,
                discord.Embed(
                    title="Spam (AI)",
                    description=(
                        f"Tin đã xóa. **Không timeout được** {member.mention} — bot cần quyền Moderate Members.\n"
                        f"Điểm: **{score}/100** — {reason}"
                    ),
                    color=discord.Color.orange(),
                ),
            )
            logger.error("[SPAM] Không có quyền timeout %s", member)
        except Exception as e:
            await self._notify_channel(
                channel,
                discord.Embed(
                    title="Lỗi sau khi xóa spam",
                    description=f"{member.mention}: {e}",
                    color=discord.Color.dark_red(),
                ),
            )
            logger.error("[SPAM] Lỗi timeout %s: %s", member, e)

    async def _handle_potential_spam_message(
        self, message: discord.Message, *, use_debug_ui: bool
    ):
        """Phân tích một tin đã xác định thuộc kênh theo dõi (realtime hoặc quét định kỳ)."""
        if message.author.bot or not message.guild:
            return

        member = message.author
        if not isinstance(member, discord.Member):
            return
        if _should_bypass(member):
            if use_debug_ui and getattr(config, "SPAM_DEBUG", False):
                print(
                    f"[SPAM][debug] Bỏ qua: {member} có Administrator hoặc Manage Messages",
                    flush=True,
                )
            return

        debug = use_debug_ui and getattr(config, "SPAM_DEBUG", False)
        if debug:
            print(
                f"[SPAM][debug] Khớp kênh theo dõi — channel_id={message.channel.id} "
                f"parent_id={getattr(message.channel, 'parent_id', None)} user={member.id}",
                flush=True,
            )
            try:
                await message.add_reaction("\N{HOURGLASS WITH FLOWING SAND}")
            except discord.HTTPException as e:
                print(f"[SPAM][debug] Không thêm reaction (cần quyền Add Reactions?): {e}", flush=True)

        try:
            async with _typing_if_supported(message.channel):
                text = _message_text_for_ai(message)
                if not text:
                    if debug:
                        raw = message.content or ""
                        print(
                            f"[SPAM][debug] Bỏ qua: không có chữ/embed để gửi AI — "
                            f"len(content)={len(raw)} embeds={len(message.embeds)} "
                            f"preview={raw[:60]!r}",
                            flush=True,
                        )
                        logger.warning(
                            "[SPAM] Nội dung rỗng: bật MESSAGE CONTENT INTENT "
                            "(Developer Portal → Bot → Privileged Gateway Intents)"
                        )
                    return

                result = await self.analyze_spam(text)

                if result is None:
                    if debug:
                        logger.warning(
                            "[SPAM][debug] Groq trả về lỗi/rỗng — kiểm tra GROQ_API_KEY và console log [SPAM]"
                        )
                    return

                score = int(result.get("score", 0))
                reason = str(result.get("reason", "Không rõ"))

                if debug:
                    logger.info(
                        "[SPAM][debug] kênh=%s parent=%s user=%s điểm=%s (ngưỡng %s) — %s",
                        message.channel.id,
                        getattr(message.channel, "parent_id", None),
                        member.id,
                        score,
                        SPAM_THRESHOLD,
                        reason[:120],
                    )

                if score < SPAM_THRESHOLD:
                    return

                await self._apply_spam_action(message, member, score, reason)
        finally:
            if debug:
                try:
                    await message.remove_reaction("\N{HOURGLASS WITH FLOWING SAND}", self.bot.user)
                except (discord.HTTPException, discord.NotFound):
                    pass

    @tasks.loop(hours=HOURLY_SCAN_INTERVAL_HOURS)
    async def hourly_watch_scan(self):
        watch_ids = _watch_channel_ids()
        if not watch_ids:
            return

        for cid in watch_ids:
            ch = self.bot.get_channel(cid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(cid)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    logger.warning("[SPAM] Quét định kỳ: không lấy được kênh %s — %s", cid, e)
                    continue

            if not isinstance(ch, _MESSAGEABLE_GUILD):
                logger.warning(
                    "[SPAM] Quét định kỳ: bỏ qua loại kênh %s (id=%s)",
                    type(ch).__name__,
                    cid,
                )
                continue

            try:
                msgs = [m async for m in ch.history(limit=HOURLY_SCAN_MESSAGE_LIMIT)]
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning("[SPAM] Quét định kỳ: không đọc history #%s — %s", cid, e)
                continue

            logger.info(
                "[SPAM] Quét định kỳ: #%s — %s tin gần nhất",
                cid,
                len(msgs),
            )
            for message in msgs:
                await self._handle_potential_spam_message(message, use_debug_ui=False)
                await asyncio.sleep(0.5)

    @hourly_watch_scan.before_loop
    async def before_hourly_watch_scan(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        watch_ids = _watch_channel_ids()
        if not _channel_matches_watch(message, watch_ids):
            return

        await self._handle_potential_spam_message(
            message, use_debug_ui=getattr(config, "SPAM_DEBUG", False)
        )


async def setup(bot):
    if not bot.intents.message_content:
        logger.warning(
            "[SPAM] Intent **message_content** đang tắt trong code — bật trong Bot.__init__ và trên Discord Developer Portal."
        )
    watch = _watch_channel_ids()
    logger.info("[SPAM] Đã load cog | theo dõi channel IDs: %s | SPAM_DEBUG=%s", watch, getattr(config, "SPAM_DEBUG", False))
    await bot.add_cog(SpamWatch(bot))






