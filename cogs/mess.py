import discord
from discord.ext import commands
from datetime import timedelta
import aiohttp
import asyncio
import json
import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

TOXICITY_THRESHOLD = 90
TIMEOUT_HOURS = 3
TIMEOUT_DELETE_RECENT_MESSAGES = 5
#1231
SYSTEM_PROMPT = """Bạn là hệ thống kiểm duyệt nội dung tiếng Việt. Nhiệm vụ của bạn là phân tích tin nhắn và đánh giá mức độ tục tĩu, xúc phạm, thù ghét.

Quy tắc chấm điểm (0-100):
- 0-30: Bình thường, không có vấn đề
- 31-50: Hơi thô nhưng chấp nhận được
- 51-89: Tục tĩu hoặc xúc phạm ở mức trung bình, gửi các liên kết sex như viet69 pornhub, pornhub, xnxx, etc.
- 90-100: Rất tục tĩu, xúc phạm nặng, thù ghét, đe dọa, bị hack nick discord (spam kênh discord.gg), tin nhắn kiểu Yoo check this out discordapp.com/invite/perfect-girls ( người dùng bị hack nên đi spam kênh),gửi các liên kết sex như viet69 pornhub, pornhub, xnxx, XAMVN,etc, xúc phạm bot với những từ như bot ngu, bot dốt...

Lưu ý:
- Phân tích cả tiếng Việt không dấu, viết tắt, teencode
- Xét ngữ cảnh: đùa giỡn nhẹ nhàng vs xúc phạm thật sự
- Các từ chửi bậy, phân biệt, kỳ thị, đe dọa bạo lực = điểm cao

Trả lời ĐÚNG định dạng JSON (không thêm gì khác):
{"score": <số_nguyên_0_đến_100>, "reason": "<giải_thích_ngắn_gọn>"}"""


class MessageModerator(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def analyze_toxicity(self, text: str) -> dict | None:
        """Gọi Groq API để phân tích mức độ tục tĩu của tin nhắn."""
        api_key = config.GROQ_API_KEY
        if not api_key:
            logger.error("[MESS] GROQ_API_KEY chưa được cấu hình trong .env")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Phân tích tin nhắn sau:\n\n\"{text}\""},
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"[MESS] Groq API error {resp.status}: {error_text}")
                        return None

                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"].strip()

                    # Parse JSON từ response, xử lý trường hợp có markdown code block
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                    result = json.loads(content)
                    return result

        except json.JSONDecodeError as e:
            logger.error(f"[MESS] Không parse được JSON từ Groq: {e} | raw: {content}")
            return None
        except Exception as e:
            logger.error(f"[MESS] Lỗi khi gọi Groq API: {e}")
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if self.bot.user not in message.mentions:
            return

        # Xác định tin nhắn cần kiểm tra
        target_message = None

        if message.reference and message.reference.message_id:
            # Người dùng reply vào 1 tin nhắn và tag bot → kiểm tra tin nhắn được reply
            try:
                target_message = await message.channel.fetch_message(message.reference.message_id)
            except (discord.NotFound, discord.Forbidden):
                await message.reply("Không tìm thấy tin nhắn được trích dẫn.", mention_author=False)
                return
        else:
            # Người dùng tag bot không reply → kiểm tra 10 tin nhắn gần nhất
            messages_to_check = []
            async for msg in message.channel.history(limit=5, before=message):
                if not msg.author.bot:
                    messages_to_check.append(msg)

            if not messages_to_check:
                await message.reply("Không tìm thấy tin nhắn nào gần đây để kiểm tra.", mention_author=False)
                return

            combined_text = "\n".join(
                f"{msg.author.display_name}: {msg.content}" for msg in messages_to_check if msg.content
            )

            if not combined_text.strip():
                await message.reply("Không có nội dung text nào gần đây để kiểm tra.", mention_author=False)
                return

            async with message.channel.typing():
                result = await self.analyze_toxicity(combined_text)

            if result is None:
                await message.reply("Lỗi khi phân tích tin nhắn. Vui lòng thử lại sau.", mention_author=False)
                return

            score = result.get("score", 0)
            reason = result.get("reason", "Không rõ")

            if score >= TOXICITY_THRESHOLD:
                # Tìm người gửi tin nhắn tục tĩu nhất (phân tích từng tin nhắn)
                worst_msg = None
                worst_score = 0
                for msg in messages_to_check:
                    if not msg.content:
                        continue
                    individual_result = await self.analyze_toxicity(msg.content)
                    if individual_result and individual_result.get("score", 0) > worst_score:
                        worst_score = individual_result.get("score", 0)
                        worst_msg = msg

                if worst_msg and worst_score >= TOXICITY_THRESHOLD:
                    await self._timeout_user(message, worst_msg, worst_score, individual_result.get("reason", reason))
                else:
                    embed = discord.Embed(
                        title="Kết quả kiểm tra tin nhắn",
                        description=f"Tổng điểm tục tĩu: **{score}/100**\nLý do: {reason}\n\nKhông có cá nhân nào vượt ngưỡng timeout ({TOXICITY_THRESHOLD}).",
                        color=discord.Color.yellow(),
                    )
                    notify = await message.reply(embed=embed, mention_author=False)
                    await asyncio.sleep(5)
                    try:
                        await notify.delete()
                    except discord.NotFound:
                        pass
            else:
                embed = discord.Embed(
                    title="Kết quả kiểm tra tin nhắn",
                    description=f"Điểm tục tĩu: **{score}/100**\nLý do: {reason}",
                    color=discord.Color.green() if score < 50 else discord.Color.yellow(),
                )
                notify = await message.reply(embed=embed, mention_author=False)
                await asyncio.sleep(5)
                try:
                    await notify.delete()
                except discord.NotFound:
                    pass
            return

        # Xử lý khi reply vào 1 tin nhắn cụ thể
        if target_message.author.bot:
            await message.reply("Không thể kiểm tra tin nhắn của bot.", mention_author=False)
            return

        if not target_message.content:
            await message.reply("Tin nhắn được trích dẫn không có nội dung text.", mention_author=False)
            return

        async with message.channel.typing():
            result = await self.analyze_toxicity(target_message.content)

        if result is None:
            await message.reply("Lỗi khi phân tích tin nhắn. Vui lòng thử lại sau.", mention_author=False)
            return

        score = result.get("score", 0)
        reason = result.get("reason", "Không rõ")

        if score >= TOXICITY_THRESHOLD:
            await self._timeout_user(message, target_message, score, reason)
        else:
            embed = discord.Embed(
                title="Kết quả kiểm tra tin nhắn",
                description=(
                    f"**Tin nhắn của {target_message.author.display_name}:**\n"
                    f"> {target_message.content[:500]}\n\n"
                    f"Điểm tục tĩu: **{score}/100**\n"
                    f"Lý do: {reason}"
                ),
                color=discord.Color.green() if score < 50 else discord.Color.yellow(),
            )
            notify = await message.reply(embed=embed, mention_author=False)
            await asyncio.sleep(5)
            try:
                await notify.delete()
            except discord.NotFound:
                pass

    async def _delete_recent_messages_from_user(
        self, channel: discord.abc.Messageable, user_id: int, limit: int = TIMEOUT_DELETE_RECENT_MESSAGES
    ) -> int:
        """Xóa tối đa `limit` tin nhắn gần nhất của user trong kênh (từ mới đến cũ)."""
        to_delete: list[discord.Message] = []
        async for msg in channel.history(limit=200):
            if msg.author.id == user_id:
                to_delete.append(msg)
                if len(to_delete) >= limit:
                    break

        deleted = 0
        for msg in to_delete:
            try:
                await msg.delete()
                deleted += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        return deleted

    async def _timeout_user(self, trigger_msg: discord.Message, toxic_msg: discord.Message, score: int, reason: str):
        """Timeout người dùng có tin nhắn tục tĩu."""
        member = toxic_msg.author
        if isinstance(member, discord.User):
            # Lấy Member object từ guild
            member = trigger_msg.guild.get_member(toxic_msg.author.id)

        if member is None:
            await trigger_msg.reply("Không tìm thấy thành viên trong server... Vui lòng thử lại sau.", mention_author=False)
            return

        # Không timeout owner/admin
        if member.guild_permissions.administrator:
            embed = discord.Embed(
                title="Phát hiện tin nhắn vi phạm",
                description=(
                    f"**Tin nhắn của {member.display_name}:**\n"
                    f"> {toxic_msg.content[:500]}\n\n"
                    f"Điểm tục tĩu: **{score}/100**\n"
                    f"Lý do: {reason}\n\n"
                    f"⚠️ Không thể timeout vì người dùng là Admin."
                ),
                color=discord.Color.orange(),
            )
            await trigger_msg.reply(embed=embed, mention_author=False)
            return

        try:
            await member.timeout(
                timedelta(hours=TIMEOUT_HOURS),
                reason=f"[Auto-mod] Tục tĩu/xúc phạm (score: {score}/100) - {reason}",
            )

            deleted_msgs = await self._delete_recent_messages_from_user(toxic_msg.channel, member.id)

            embed = discord.Embed(
                title="Đã timeout người dùng vi phạm",
                description=(
                    f"**Người vi phạm:** {member.mention}\n"
                    f"**Tin nhắn:**\n> {toxic_msg.content[:500]}\n\n"
                    f"Điểm tục tĩu: **{score}/100**\n"
                    f"Lý do: {reason}\n"
                    f"Thời gian timeout: **{TIMEOUT_HOURS} giờ**\n"
                    f"Đã xóa **{deleted_msgs}** tin nhắn gần nhất của người này trong kênh (tối đa {TIMEOUT_DELETE_RECENT_MESSAGES})."
                ),
                color=discord.Color.red(),
            )
            notify = await trigger_msg.reply(embed=embed, mention_author=False)
            logger.info(f"[MESS] Timeout {member.name} for {TIMEOUT_HOURS}h (score: {score}, reason: {reason})")

            await asyncio.sleep(5)
            try:
                await notify.delete()
            except discord.NotFound:
                pass

        except discord.Forbidden:
            await trigger_msg.reply(
                f"Bot không có quyền timeout {member.mention}. Kiểm tra lại quyền của bot.",
                mention_author=False,
            )
            logger.error(f"[MESS] Không có quyền timeout {member.name}")
        except Exception as e:
            await trigger_msg.reply(f"Lỗi khi timeout: {e}", mention_author=False)
            logger.error(f"[MESS] Lỗi timeout {member.name}: {e}")


async def setup(bot):
    await bot.add_cog(MessageModerator(bot))
