from __future__ import annotations

import json
import re
from datetime import timedelta

import aiohttp
import discord
from discord.ext import commands

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TEXT_MODEL = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

DEFAULT_SCAN_LIMIT = 5
MIN_SCAN_LIMIT = 5
MAX_SCAN_LIMIT = 10
SPAM_SCORE_THRESHOLD = 70
MAX_TEXT_CHARS = 2600
MAX_IMAGE_INPUTS = 4
DELETE_MAX_AGE = timedelta(days=1)
TIMEOUT_DAYS = 20

URL_RE = re.compile(r"https?://\S+|discord(?:\.gg|app\.com/invite|\.com/invite)/\S+", re.I)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
SUSPICIOUS_KEYWORDS = (
    "free nitro",
    "discord.gg",
    "discord.com/invite",
    "claim",
    "bonus",
    "activate code",
    "airdrop",
    "crypto",
    "usdt",
    "btc",
    "withdraw",
    "profit",
    "giveaway",
    "mrbeast",
    "check this out",
    "casino",
    "rakeback",
)
SCAM_REASON_KEYWORDS = (
    "spam",
    "scam",
    "crypto",
    "casino",
    "phishing",
    "airdrop",
    "free nitro",
    "invite",
    "bonus",
    "withdraw",
    "hack",
)

SYSTEM_PROMPT = """
You are a Discord moderation assistant focused on scam and spam cleanup.

Classify one Discord message at a time. Consider the message text, embeds, links,
attachment filenames, and images if they are provided.

Mark as spam when the message looks like:
- hacked-account promo blasts
- fake giveaway / free nitro / invite bait
- crypto, casino, bonus, rakeback, withdrawal-proof scams
- repetitive ad spam or clickbait screenshots
- suspicious "check this out" style social engineering

Do not mark as spam for normal chat, harmless memes, ordinary image sharing, or
legitimate one-off links without scam indicators.

Return JSON only in this exact shape:
{"is_spam": true, "score": 0, "reason": "short reason"}
""".strip()


def _is_protected_member(author: discord.abc.User) -> bool:
    if not isinstance(author, discord.Member):
        return False
    perms = author.guild_permissions
    return perms.administrator or perms.manage_messages


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    filename = attachment.filename.lower()
    return content_type.startswith("image/") or filename.endswith(IMAGE_EXTENSIONS)


def _message_text(message: discord.Message) -> str:
    parts: list[str] = []

    if message.content and message.content.strip():
        parts.append(message.content.strip())

    for embed in message.embeds:
        if embed.title:
            parts.append(f"embed_title: {embed.title}")
        if embed.description:
            parts.append(f"embed_description: {embed.description}")
        if embed.author and embed.author.name:
            parts.append(f"embed_author: {embed.author.name}")
        for field in embed.fields:
            if field.name:
                parts.append(f"embed_field_name: {field.name}")
            if field.value:
                parts.append(f"embed_field_value: {field.value}")
        if embed.footer and embed.footer.text:
            parts.append(f"embed_footer: {embed.footer.text}")
        if embed.url:
            parts.append(f"embed_url: {embed.url}")

    for attachment in message.attachments:
        meta = f"attachment: name={attachment.filename}"
        if attachment.content_type:
            meta += f" type={attachment.content_type}"
        parts.append(meta)

    for sticker in message.stickers:
        parts.append(f"sticker: name={sticker.name}")

    text = "\n".join(part for part in parts if part).strip()
    return text[:MAX_TEXT_CHARS]


def _image_urls(message: discord.Message) -> list[str]:
    urls: list[str] = []

    for attachment in message.attachments:
        if _is_image_attachment(attachment):
            urls.append(attachment.url)

    for embed in message.embeds:
        if embed.image and embed.image.url:
            urls.append(embed.image.url)
        if embed.thumbnail and embed.thumbnail.url:
            urls.append(embed.thumbnail.url)

    unique_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)
        if len(unique_urls) >= MAX_IMAGE_INPUTS:
            break
    return unique_urls


def _looks_like_candidate(message: discord.Message) -> bool:
    if message.stickers:
        return True
    if message.attachments or message.embeds:
        return True

    text = _message_text(message).lower()
    if not text:
        return False

    if URL_RE.search(text):
        return True
    if any(keyword in text for keyword in SUSPICIOUS_KEYWORDS):
        return True

    return False


def _coerce_result(payload: dict) -> dict | None:
    try:
        score = int(payload.get("score", 0))
    except (TypeError, ValueError):
        score = 0

    score = max(0, min(100, score))
    raw_is_spam = payload.get("is_spam", False)
    if isinstance(raw_is_spam, str):
        is_spam = raw_is_spam.strip().lower() in {"true", "1", "yes"}
    else:
        is_spam = bool(raw_is_spam)
    reason = str(payload.get("reason", "No reason")).strip() or "No reason"

    lowered_reason = reason.lower()
    reason_looks_scam = any(keyword in lowered_reason for keyword in SCAM_REASON_KEYWORDS)

    # Groq vision doi khi tra ly do rat ro la scam/spam nhung score = 0.
    # Neu model da xac nhan spam hoac ly do mang tinh scam manh, day diem len tren nguong de xu ly.
    if is_spam and score <= SPAM_SCORE_THRESHOLD:
        score = SPAM_SCORE_THRESHOLD + 5
    elif score == 0 and reason_looks_scam:
        score = SPAM_SCORE_THRESHOLD + 5

    return {"is_spam": is_spam, "score": score, "reason": reason}


def _message_preview(message: discord.Message) -> str:
    preview = _message_text(message).replace("\n", " ").strip()
    if preview:
        return preview[:180] + ("..." if len(preview) > 180 else "")

    image_count = len(_image_urls(message))
    if image_count:
        return f"(message contains {image_count} image file(s))"
    if message.stickers:
        return "(message contains sticker(s))"
    return "(no readable text)"


def _score_requires_action(score: int) -> bool:
    return score > SPAM_SCORE_THRESHOLD


class ClearSpamImage(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _request_groq(self, payload: dict) -> dict | None:
        api_key = getattr(config, "GROQ_API_KEY", "")
        if not api_key:
            logger.error("[CLEAR] GROQ_API_KEY is missing")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        raw_content = ""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(GROQ_API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error("[CLEAR] Groq API error %s: %s", resp.status, error_text)
                        return None

                    data = await resp.json()
                    raw_content = data["choices"][0]["message"]["content"].strip()
                    if raw_content.startswith("```"):
                        raw_content = raw_content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    return _coerce_result(json.loads(raw_content))
        except json.JSONDecodeError as exc:
            logger.error("[CLEAR] Invalid JSON from Groq: %s | raw=%r", exc, raw_content)
            return None
        except Exception as exc:
            logger.error("[CLEAR] Groq request failed: %s", exc)
            return None

    async def _send_temp_reply(
        self,
        trigger: discord.Message,
        content: str,
        *,
        delete_after: float = 12.0,
    ) -> None:
        try:
            await trigger.reply(content, mention_author=False, delete_after=delete_after)
        except discord.HTTPException:
            return

    async def _timeout_member(
        self,
        member: discord.Member,
        *,
        score: int,
        reason: str,
    ) -> tuple[bool, str]:
        guild = member.guild
        me = guild.me or guild.get_member(self.bot.user.id if self.bot.user else 0)

        if member.id == guild.owner_id:
            return False, "khong the timeout chu server"
        if me is None:
            return False, "khong tim thay member cua bot trong server"
        if not me.guild_permissions.moderate_members:
            return False, "bot thieu quyen Moderate Members"
        if member.top_role >= me.top_role:
            return False, "vai tro cua nguoi nay cao hon hoac bang bot"

        try:
            await member.timeout(
                timedelta(days=TIMEOUT_DAYS),
                reason=f"[Auto spam clear] score={score} - {reason}",
            )
            return True, f"da timeout {TIMEOUT_DAYS} ngay"
        except discord.Forbidden:
            return False, "bot khong du quyen timeout"
        except discord.HTTPException as exc:
            return False, f"loi timeout: {exc}"

    async def _apply_spam_actions(
        self,
        message: discord.Message,
        *,
        score: int,
        reason: str,
    ) -> dict:
        deleted = await self._delete_messages(message.channel, [message])

        timeout_ok = False
        timeout_note = "khong timeout"
        author = message.author
        if isinstance(author, discord.Member) and not _is_protected_member(author):
            timeout_ok, timeout_note = await self._timeout_member(
                author,
                score=score,
                reason=reason,
            )
        elif isinstance(author, discord.Member):
            timeout_note = "bo qua timeout vi day la mod/admin"

        return {
            "deleted": deleted,
            "timeout_ok": timeout_ok,
            "timeout_note": timeout_note,
        }

    async def _analyze_message(self, message: discord.Message) -> dict | None:
        text = _message_text(message)
        image_urls = _image_urls(message)

        if not text and not image_urls:
            return None

        user_prompt = (
            f"Author display name: {message.author.display_name}\n"
            f"Message text and metadata:\n{text or '(no text)'}\n\n"
            "Decide if this is scam/spam that should be removed from a Discord server."
        )

        if image_urls:
            vision_payload = {
                "model": GROQ_VISION_MODEL,
                "temperature": 0,
                "max_completion_tokens": 180,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            *[
                                {"type": "image_url", "image_url": {"url": url}}
                                for url in image_urls
                            ],
                        ],
                    },
                ],
            }
            result = await self._request_groq(vision_payload)
            if result is not None:
                return result

        text_payload = {
            "model": GROQ_TEXT_MODEL,
            "temperature": 0,
            "max_completion_tokens": 180,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        return await self._request_groq(text_payload)

    async def _process_single_target(
        self,
        trigger_message: discord.Message,
        target_message: discord.Message,
    ) -> None:
        if target_message.author.bot:
            await self._send_temp_reply(
                trigger_message,
                "Khong quet tin nhan cua bot.",
            )
            return

        result = await self._analyze_message(target_message)
        if result is None:
            await self._send_temp_reply(
                trigger_message,
                "Khong doc duoc noi dung tin nhan nay de phan tich.",
            )
            return

        score = result["score"]
        reason = result["reason"]
        preview = _message_preview(target_message)

        if _is_protected_member(target_message.author):
            await self._send_temp_reply(
                trigger_message,
                (
                    f"Tin nhan cua {target_message.author.display_name} duoc cham {score}/100"
                    f" ({reason}) nhung nguoi nay la mod/admin nen bot khong tu dong xoa.\n"
                    f"Noi dung: {preview}"
                ),
                delete_after=18,
            )
            return

        if not _score_requires_action(score):
            await self._send_temp_reply(
                trigger_message,
                (
                    f"Tin nhan nay chua dat nguong xu ly. Diem {score}/100 - {reason}\n"
                    f"Noi dung: {preview}"
                ),
                delete_after=15,
            )
            return

        action = await self._apply_spam_actions(
            target_message,
            score=score,
            reason=reason,
        )
        if action["deleted"] == 0:
            await self._send_temp_reply(
                trigger_message,
                (
                    f"Groq tra {score}/100 - {reason} nhung bot khong xoa duoc tin nhan. "
                    f"Trang thai timeout: {action['timeout_note']}."
                ),
                delete_after=18,
            )
            return

        await self._send_temp_reply(
            trigger_message,
            (
                f"Da xoa tin spam cua {target_message.author.display_name}. "
                f"Diem {score}/100 - {reason}. Timeout: {action['timeout_note']}."
            ),
            delete_after=18,
        )
        logger.info(
            "[CLEAR] mention scan removed message=%s channel=%s author=%s score=%s timeout=%s",
            target_message.id,
            target_message.channel.id,
            target_message.author.id,
            score,
            action["timeout_note"],
        )

    async def _delete_messages(
        self, channel: discord.TextChannel | discord.Thread, messages: list[discord.Message]
    ) -> int:
        if not messages:
            return 0

        now = discord.utils.utcnow()
        fresh: list[discord.Message] = []
        stale: list[discord.Message] = []

        for msg in messages:
            if now - msg.created_at <= DELETE_MAX_AGE:
                fresh.append(msg)
            else:
                stale.append(msg)

        deleted = 0

        async def _delete_one(msg: discord.Message) -> None:
            nonlocal deleted
            try:
                await msg.delete()
                deleted += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if len(fresh) == 1:
            await _delete_one(fresh[0])
        elif len(fresh) > 1:
            for index in range(0, len(fresh), 100):
                chunk = fresh[index : index + 100]
                if len(chunk) == 1:
                    await _delete_one(chunk[0])
                    continue
                try:
                    await channel.delete_messages(chunk)
                    deleted += len(chunk)
                except (discord.Forbidden, discord.HTTPException):
                    for msg in chunk:
                        await _delete_one(msg)

        for msg in stale:
            await _delete_one(msg)

        return deleted

    async def _collect_reviewable_messages(
        self,
        ctx: commands.Context,
        processing: discord.Message,
        amount: int,
    ) -> tuple[list[discord.Message], int]:
        history_limit = min(max(amount * 5, 50), MAX_SCAN_LIMIT * 6)
        reviewable: list[discord.Message] = []
        history_seen = 0

        async for message in ctx.channel.history(limit=history_limit):
            history_seen += 1

            if message.id == processing.id:
                continue
            if message.author.bot:
                continue
            if message.type not in (discord.MessageType.default, discord.MessageType.reply):
                continue

            reviewable.append(message)
            if len(reviewable) >= amount:
                break

        # Neu sau khi xoa lenh ma command message van con trong history, bo no ra o buoc cuoi.
        reviewable = [message for message in reviewable if message.id != ctx.message.id]
        if len(reviewable) > amount:
            reviewable = reviewable[:amount]

        return reviewable, history_seen

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if self.bot.user is None or self.bot.user not in message.mentions:
            return
        if message.content.startswith("!"):
            return

        author = message.author
        if not isinstance(author, discord.Member):
            return

        if not message.reference or not message.reference.message_id:
            await self._send_temp_reply(
                message,
                "Hay reply vao tin can quet roi tag bot. Khi do bot chi quet dung tin do.",
            )
            return

        try:
            target_message = await message.channel.fetch_message(message.reference.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await self._send_temp_reply(
                message,
                "Khong tim thay tin nhan duoc reply de quet.",
            )
            return

        async with message.channel.typing():
            await self._process_single_target(message, target_message)

    @commands.command(name="clear")
    @commands.guild_only()
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear_spam(self, ctx: commands.Context, amount: int = DEFAULT_SCAN_LIMIT):
        amount = max(MIN_SCAN_LIMIT, min(MAX_SCAN_LIMIT, amount))

        if not isinstance(ctx.channel, (discord.TextChannel, discord.Thread)):
            await ctx.send("Lenh nay chi dung duoc trong text channel hoac thread.")
            return

        if not getattr(config, "GROQ_API_KEY", ""):
            await ctx.send("Chua cau hinh GROQ_API_KEY nen khong the dung !clear.")
            return

        processing = await ctx.send(
            f"Dang quet toi da {amount} tin nhan gan day bang Groq de tim spam..."
        )

        try:
            try:
                await ctx.message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

            reviewable, history_seen = await self._collect_reviewable_messages(
                ctx, processing, amount
            )
            inspected = len(reviewable)
            candidates = [message for message in reviewable if _looks_like_candidate(message)]

            if not candidates and reviewable:
                # Fallback: van gui AI mot it tin gan nhat de tranh truong hop media message
                # hoac dang message Discord client hien khac voi heuristic.
                candidates = reviewable[: min(10, len(reviewable))]

            logger.info(
                "[CLEAR] pre-scan channel=%s history_seen=%s reviewable=%s candidates=%s",
                ctx.channel.id,
                history_seen,
                inspected,
                len(candidates),
            )

            if not candidates:
                await processing.edit(
                    content=(
                        f"Khong thay ung vien spam. Da doc {history_seen} tin lich su, "
                        f"loc duoc {inspected} tin nguoi dung de xem xet."
                    )
                )
                await processing.delete(delay=10)
                return

            flagged: list[tuple[discord.Message, int, str]] = []
            reasons: list[str] = []

            for message in candidates:
                if _is_protected_member(message.author):
                    continue

                result = await self._analyze_message(message)
                if result is None:
                    continue

                score = result["score"]
                if not _score_requires_action(score):
                    continue

                flagged.append((message, score, result["reason"]))
                if len(reasons) < 5:
                    snippet = _message_text(message).replace("\n", " ").strip()
                    if len(snippet) > 80:
                        snippet = snippet[:77] + "..."
                    if not snippet:
                        snippet = "(image-only spam)"
                    reasons.append(
                        f"{message.author.display_name}: {score}/100 - {result['reason']} | {snippet}"
                    )

            if not flagged:
                await processing.edit(
                    content=(
                        f"Da quet {inspected} tin gan day va gui Groq {len(candidates)} ung vien, "
                        "nhung khong thay tin nao dat nguong spam de xoa."
                    )
                )
                await processing.delete(delay=12)
                return

            flagged_messages = [message for message, _, _ in flagged]
            deleted = await self._delete_messages(ctx.channel, flagged_messages)

            timeout_success = 0
            timeout_failed: list[str] = []
            timed_out_ids: set[int] = set()

            for message, score, reason in flagged:
                author = message.author
                if not isinstance(author, discord.Member):
                    continue
                if author.id in timed_out_ids:
                    continue
                if _is_protected_member(author):
                    timeout_failed.append(f"{author.display_name}: bo qua vi la mod/admin")
                    timed_out_ids.add(author.id)
                    continue

                timeout_ok, timeout_note = await self._timeout_member(
                    author,
                    score=score,
                    reason=reason,
                )
                timed_out_ids.add(author.id)
                if timeout_ok:
                    timeout_success += 1
                else:
                    timeout_failed.append(f"{author.display_name}: {timeout_note}")

            if deleted == 0 and timeout_success == 0:
                await processing.edit(
                    content=(
                        f"Da quet {inspected} tin, gui Groq {len(candidates)} ung vien "
                        "nhung khong xoa hay timeout duoc truong hop nao."
                    )
                )
                await processing.delete(delay=12)
                return

            unique_authors = len({message.author.id for message, _, _ in flagged})
            summary = (
                f"Da xu ly spam voi nguong diem > {SPAM_SCORE_THRESHOLD}. "
                f"Xoa {deleted} tin, timeout {timeout_success}/{unique_authors} tai khoan trong {TIMEOUT_DAYS} ngay. "
                f"Groq da danh gia {len(candidates)} ung vien sau khi quet {inspected} tin gan day."
            )
            if reasons:
                summary += "\n" + "\n".join(f"- {reason}" for reason in reasons)
            if timeout_failed:
                summary += "\n" + "\n".join(
                    f"- Timeout that bai: {note}" for note in timeout_failed[:5]
                )

            await processing.edit(content=summary[:1900])
            await processing.delete(delay=20)
            logger.info(
                "[CLEAR] %s ran !clear in channel=%s | scanned=%s candidates=%s deleted=%s",
                ctx.author,
                ctx.channel.id,
                inspected,
                len(candidates),
                deleted,
            )
        except Exception as exc:
            logger.error("[CLEAR] !clear failed: %s", exc, exc_info=True)
            await processing.edit(content=f"Loi khi chay !clear: {exc}")

    @clear_spam.error
    async def clear_spam_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send(
                "Bot can quyen Manage Messages va Read Message History de dung !clear."
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"Cach dung: `!clear [so_tin]` (mac dinh {DEFAULT_SCAN_LIMIT}).")
            return

        logger.error("[CLEAR] Command error: %s", error, exc_info=True)
        await ctx.send(f"Loi !clear: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ClearSpamImage(bot))
