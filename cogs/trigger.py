"""Lệnh rút gọn tùy chỉnh: !insert <tên> <nội dung (vd. link CDN dài)>, sau đó gọi !<tên>.

Lưu MongoDB (collection trigger_aliases). !insert / !showcase / !delete1 và gọi alias rút gọn:
chỉ member có một trong TRIGGER_ROLE_IDS.

Nếu config.TRIGGER_GLOBAL_POOL = True, mọi server/kênh dùng chung một kho trong Mongo.

Nếu TRIGGER_GLOBAL_POOL = False và cấu hình TRIGGER_SHARED_GUILD_IDS, chỉ các server trong
danh sách đó dùng chung một kho (lệnh thêm ở server A cũng gọi được ở server B).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils.logger import setup_logger

logger = setup_logger(__name__)

MONGO_COLLECTION = "trigger_aliases"

# Role được phép !insert / !showcase / !delete1 / gọi lệnh rút gọn (theo yêu cầu)
TRIGGER_ROLE_IDS = {1469579723196727441, 1241969973086388244,1185158470958333953,1482031406468304926}

# Tên lệnh hệ thống — không cho đặt alias trùng (tránh nhầm)
_RESERVED_ALIASES = frozenset(
    {
        "insert",
        "showcase",
        "delete1",
        "help",
        "sync",
        "cogs",
    }
)

_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")

# Một tin Discord tối đa ~2000 ký tự; Mongo lưu dư đầu cho link/query dài
MAX_TRIGGER_CONTENT = 8192
_DISCORD_CHUNK = 1900

# guild_id trong Mongo khi TRIGGER_GLOBAL_POOL — không trùng ID server thật
_GLOBAL_TRIGGER_GUILD_ID = 0


async def _send_in_chunks(sendable: discord.abc.Messageable, text: str) -> None:
    """Gửi nội dung; chia nhiều tin nếu vượt giới hạn Discord."""
    if not text:
        return
    if len(text) <= _DISCORD_CHUNK:
        await sendable.send(text)
        return
    for i in range(0, len(text), _DISCORD_CHUNK):
        await sendable.send(text[i : i + _DISCORD_CHUNK])


def _member_allowed(member: discord.Member) -> bool:
    role_ids = {r.id for r in member.roles}
    return bool(role_ids & TRIGGER_ROLE_IDS)


def _effective_guild_id(guild_id: int) -> int:
    """Guild id dùng khi đọc/ghi Mongo: global pool hoặc nhóm server trong TRIGGER_SHARED_GUILD_IDS."""
    if getattr(config, "TRIGGER_GLOBAL_POOL", False):
        return _GLOBAL_TRIGGER_GUILD_ID
    shared = getattr(config, "TRIGGER_SHARED_GUILD_IDS", ()) or ()
    if not shared or guild_id not in shared:
        return guild_id
    return min(shared)


class Trigger(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mongo: Optional[AsyncIOMotorClient] = None
        self._db = None

    async def cog_load(self) -> None:
        if not config.MONGO_URI:
            logger.warning("[TRIGGER] MONGO_URI not set — trigger aliases disabled")
            return
        self._mongo = AsyncIOMotorClient(
            config.MONGO_URI,
            tls=True,
            tlsAllowInvalidCertificates=True,
            serverSelectionTimeoutMS=10_000,
        )
        self._db = self._mongo[config.MONGO_DB_NAME]
        await self._db[MONGO_COLLECTION].create_index(
            [("guild_id", 1), ("alias", 1)], unique=True
        )
        logger.info(
            "[TRIGGER] MongoDB connected — db=%s, collection=%s",
            config.MONGO_DB_NAME,
            MONGO_COLLECTION,
        )

    async def cog_unload(self) -> None:
        if self._mongo:
            self._mongo.close()
            self._mongo = None
            self._db = None

    async def _get_doc(self, guild_id: int, alias: str) -> Optional[Dict[str, Any]]:
        if self._db is None:
            return None
        return await self._db[MONGO_COLLECTION].find_one(
            {"guild_id": guild_id, "alias": alias.lower()}
        )

    async def _list_guild(self, guild_id: int) -> List[Dict[str, Any]]:
        if self._db is None:
            return []
        cur = self._db[MONGO_COLLECTION].find({"guild_id": guild_id}).sort("alias", 1)
        return await cur.to_list(length=500)

    @commands.command(name="insert")
    async def insert_alias(
        self, ctx: commands.Context, alias: str, *, content: str
    ) -> None:
        """!insert <tên> <nội_dung> — toàn bộ sau <tên> là một chuỗi (link Discord CDN dài, có query `?`…)."""
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return
        if not _member_allowed(ctx.author):
            await ctx.message.add_reaction("❌")
            return
        if self._db is None:
            await ctx.send("Chưa cấu hình MongoDB (`MONGO_URI`). Không thể lưu lệnh.")
            return

        alias_norm = alias.strip().lower()
        content = content.strip()
        if not content:
            await ctx.send(
                "Nội dung không được để trống. Cú pháp: `!insert <tên> <link hoặc text đầy đủ>`\n"
                "Ví dụ: `!insert hoc_mp4 https://cdn.discordapp.com/attachments/.../file.mp4?ex=...`"
            )
            return
        if not _ALIAS_RE.match(alias_norm):
            if alias_norm.startswith(("http://", "https://")):
                await ctx.send(
                    "Cú pháp: **tên lệnh trước**, **link sau** (để link dài/query `?` không bị cắt).\n"
                    "Ví dụ: `!insert hoc_mp4 https://cdn.discordapp.com/attachments/.../file.mp4?ex=...`"
                )
            else:
                await ctx.send(
                    "Tên lệnh chỉ gồm chữ, số, `_`, `-` và tối đa 32 ký tự. Ví dụ: `auto1`."
                )
            return
        if alias_norm in _RESERVED_ALIASES:
            await ctx.send(f"Tên `{alias_norm}` là tên hệ thống, hãy chọn tên khác.")
            return

        if len(content) > MAX_TRIGGER_CONTENT:
            content = content[:MAX_TRIGGER_CONTENT]

        now = datetime.now(timezone.utc)
        gid = _effective_guild_id(ctx.guild.id)
        doc = {
            "guild_id": gid,
            "alias": alias_norm,
            "content": content,
            "created_by": ctx.author.id,
            "updated_at": now,
        }
        await self._db[MONGO_COLLECTION].update_one(
            {"guild_id": gid, "alias": alias_norm},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        await ctx.send(
            f"Đã lưu: `!{discord.utils.escape_markdown(alias_norm)}` → {discord.utils.escape_markdown(content[:500])}"
            + ("…" if len(content) > 500 else "")
        )
        logger.info(
            "[TRIGGER] upsert guild=%s (store=%s) alias=%s by=%s",
            ctx.guild.id,
            gid,
            alias_norm,
            ctx.author.id,
        )

    @insert_alias.error
    async def insert_alias_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "Thiếu tên hoặc nội dung. Dùng: `!insert <tên> <link hoặc text đầy đủ>`\n"
                "Ví dụ: `!insert hoc_mp4 https://cdn.discordapp.com/attachments/.../file.mp4?ex=...`"
            )
            return
        raise error

    @commands.command(name="showcase")
    async def showcase(self, ctx: commands.Context) -> None:
        """!showcase — xem các lệnh rút gọn đã lưu trong server."""
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return
        if not _member_allowed(ctx.author):
            await ctx.message.add_reaction("❌")
            return
        if self._db is None:
            await ctx.send("Chưa cấu hình MongoDB (`MONGO_URI`).")
            return

        rows = await self._list_guild(_effective_guild_id(ctx.guild.id))
        if not rows:
            await ctx.send("Chưa có lệnh rút gọn nào. Dùng `!insert <tên> <link>` để thêm.")
            return

        lines: List[str] = []
        for r in rows:
            a = str(r.get("alias", "?"))
            raw = str(r.get("content", ""))
            c = raw[:120]
            suffix = "…" if len(raw) > 120 else ""
            lines.append(f"• `!{a}` → {c}{suffix}")

        header = "**Lệnh đã lưu:**\n"
        current: List[str] = []
        size = len(header)
        for line in lines:
            extra = len(line) if not current else 1 + len(line)
            if current and size + extra > 1900:
                await ctx.send(header + "\n".join(current))
                current = [line]
                size = len(header) + len(line)
            else:
                if not current:
                    size = len(header) + len(line)
                else:
                    size += 1 + len(line)
                current.append(line)
        if current:
            await ctx.send(header + "\n".join(current))

    @commands.command(name="delete1")
    async def delete_alias(self, ctx: commands.Context, alias: str) -> None:
        """!delete1 <tên> — xóa lệnh rút gọn đã lưu (ví dụ: `!delete1 auto1`)."""
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return
        if not _member_allowed(ctx.author):
            await ctx.message.add_reaction("❌")
            return
        if self._db is None:
            await ctx.send("Chưa cấu hình MongoDB (`MONGO_URI`).")
            return

        alias_norm = alias.strip().lower()
        if not alias_norm:
            await ctx.send("Cần tên lệnh. Ví dụ: `!delete1 auto1`")
            return
        if not _ALIAS_RE.match(alias_norm):
            await ctx.send("Tên lệnh không hợp lệ.")
            return

        gid = _effective_guild_id(ctx.guild.id)
        result = await self._db[MONGO_COLLECTION].delete_one(
            {"guild_id": gid, "alias": alias_norm}
        )
        if result.deleted_count:
            await ctx.send(f"Đã xóa lệnh rút gọn `!{discord.utils.escape_markdown(alias_norm)}`.")
            logger.info(
                "[TRIGGER] delete guild=%s (store=%s) alias=%s by=%s",
                ctx.guild.id,
                gid,
                alias_norm,
                ctx.author.id,
            )
        else:
            await ctx.send(
                f"Không có lệnh `!{discord.utils.escape_markdown(alias_norm)}` trong server này."
            )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if not isinstance(error, commands.CommandNotFound):
            return
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        invoked = (ctx.invoked_with or "").strip().lower()
        if not invoked:
            return
        if not _member_allowed(ctx.author):
            return
        if self._db is None:
            return

        doc = await self._get_doc(_effective_guild_id(ctx.guild.id), invoked)
        if doc is None:
            return

        out = str(doc.get("content", "")).strip()
        if not out:
            return
        try:
            await _send_in_chunks(ctx.channel, out)
        except discord.HTTPException as e:
            logger.error("[TRIGGER] send failed: %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trigger(bot))
