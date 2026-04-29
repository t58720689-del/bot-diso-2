"""Tài liệu đóng góp: !append (theo role), !docs / !tailieu xem danh sách.

Lưu MongoDB (motor + config.MONGO_URI / MONGO_DB_NAME). Không có URI → fallback data/documents.json.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from utils.logger import setup_logger

logger = setup_logger(__name__)

DATA_DIR = Path("data")
DOCS_FILE = DATA_DIR / "documents.json"
MONGO_COLLECTION = "contributed_documents"

# Role được phép dùng !append (Developer Mode: chuột phải role → Copy Role ID)
DOC_APPEND_ROLE_IDS = {1472560579007746079,123}


def _member_can_append(member: discord.Member) -> bool:
    role_ids = {r.id for r in member.roles}
    return bool(role_ids & DOC_APPEND_ROLE_IDS)


def _load_docs() -> Dict[str, Any]:
    try:
        with DOCS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    items = data.get("documents")
    if not isinstance(items, list):
        items = []
    data["documents"] = items
    return data


def _save_docs(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DOCS_FILE.with_suffix(DOCS_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DOCS_FILE)


def _doc_to_dict(d: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(d, dict):
        return None
    return {
        "url": d.get("url", ""),
        "title": d.get("title", ""),
        "added_by": d.get("added_by"),
        "added_by_display_name": d.get("added_by_display_name"),
        "added_at": d.get("added_at"),
    }


class Documents(commands.Cog):
    """!append / !docs (!tailieu) — kho tài liệu đóng góp."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mongo: Optional[AsyncIOMotorClient] = None
        self._db = None

    async def cog_load(self) -> None:
        if config.MONGO_URI:
            self._mongo = AsyncIOMotorClient(
                config.MONGO_URI,
                tls=True,
                tlsAllowInvalidCertificates=True,
                serverSelectionTimeoutMS=10_000,
            )
            self._db = self._mongo[config.MONGO_DB_NAME]
            await self._db[MONGO_COLLECTION].create_index("added_at")
            logger.info(
                "[DOC] MongoDB connected — db=%s, collection=%s",
                config.MONGO_DB_NAME,
                MONGO_COLLECTION,
            )
        else:
            logger.warning("[DOC] MONGO_URI not set — dùng file %s", DOCS_FILE)

    async def cog_unload(self) -> None:
        if self._mongo:
            self._mongo.close()
            self._mongo = None
            self._db = None

    async def _all_docs_ordered(self) -> List[Dict[str, Any]]:
        if self._db is not None:
            cur = self._db[MONGO_COLLECTION].find().sort("added_at", 1)
            raw = await cur.to_list(length=None)
            out: List[Dict[str, Any]] = []
            for d in raw:
                x = _doc_to_dict(d)
                if x and x.get("url"):
                    out.append(x)
            return out
        data = _load_docs()
        return [x for x in data["documents"] if isinstance(x, dict) and x.get("url")]

    async def _contributor_label(
        self, guild: Optional[discord.Guild], doc: Dict[str, Any]
    ) -> str:
        """Tên hiển thị, không mention (plain text)."""
        stored = doc.get("added_by_display_name")
        if isinstance(stored, str) and stored.strip():
            return discord.utils.escape_markdown(stored.strip()[:100])

        uid = doc.get("added_by")
        if uid is None:
            return "(không rõ)"
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            return "(không rõ)"

        if guild is not None:
            m = guild.get_member(uid_int)
            if m is None:
                try:
                    m = await guild.fetch_member(uid_int)
                except (discord.NotFound, discord.HTTPException):
                    m = None
            if m is not None:
                return discord.utils.escape_markdown(m.display_name)

        u = self.bot.get_user(uid_int)
        if u is not None:
            name = u.global_name or u.name
            return discord.utils.escape_markdown(name)
        return discord.utils.escape_markdown(f"ID {uid_int}")

    @commands.command(name="append")
    async def append_document(self, ctx: commands.Context, link: str, *, title: str = ""):
        """!append <link> [tiêu đề] — thêm tài liệu; tiêu đề tùy chọn; link không bắt buộc http(s)."""
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.message.add_reaction("❌")
            return
        if not _member_can_append(ctx.author):
            await ctx.message.add_reaction("❌")
            return

        link = link.strip()
        title = (title or "").strip()
        if not link:
            await ctx.send("Cần có link. Ví dụ: `!append drive.google.com/...` hoặc `!append https://... Tên tài liệu`")
            return

        if len(title) > 300:
            title = title[:300] + "…"

        now = datetime.now(timezone.utc)
        display_name = (ctx.author.display_name or ctx.author.name or "")[:100]
        entry = {
            "url": link,
            "title": title,
            "added_by": ctx.author.id,
            "added_by_display_name": display_name,
            "added_at": now,
        }

        if self._db is not None:
            await self._db[MONGO_COLLECTION].insert_one(entry)
        else:
            data = _load_docs()
            docs: List[Dict[str, Any]] = data["documents"]
            entry_json = {
                "url": link,
                "title": title,
                "added_by": ctx.author.id,
                "added_by_display_name": display_name,
                "added_at": now.isoformat(),
            }
            docs.append(entry_json)
            _save_docs(data)

        logger.info("Doc added by %s: %s | %s", ctx.author.id, (title or "(no title)")[:50], link[:80])
        if title:
            await ctx.send(f"Đã thêm tài liệu: **{discord.utils.escape_markdown(title)}**")
        else:
            await ctx.send(
                f"Đã thêm tài liệu (chưa đặt tiêu đề): {discord.utils.escape_markdown(link[:500])}"
            )

    @commands.command(name="docs", aliases=["tailieu", "tailieu_dong_gop"])
    async def list_documents(self, ctx: commands.Context):
        """!docs | !tailieu — xem danh sách tài liệu đã đóng góp (ai cũng xem được)."""
        docs = await self._all_docs_ordered()
        if not docs:
            await ctx.send("Chưa có tài liệu nào. Thành viên có role được phép dùng `!append` để thêm.")
            return

        labels = await asyncio.gather(
            *[self._contributor_label(ctx.guild, d) for d in docs]
        )
        lines: List[str] = []
        for i, (d, who) in enumerate(zip(docs, labels), start=1):
            t = discord.utils.escape_markdown(str(d.get("title") or "(không tiêu đề)"))
            u = str(d.get("url") or "")
            lines.append(f"{i}. **{t}**\n   {u}\n   Thêm bởi: {who}")

        chunk = "\n\n".join(lines)
        if len(chunk) <= 1900:
            await ctx.send(chunk)
            return

        current: List[str] = []
        size = 0
        for block in lines:
            b = "\n\n" + block if current else block
            if size + len(b) > 1900 and current:
                await ctx.send("\n\n".join(current))
                current = [block]
                size = len(block)
            else:
                if not current:
                    size = len(block)
                else:
                    size += len(b)
                current.append(block)
        if current:
            await ctx.send("\n\n".join(current))


async def setup(bot: commands.Bot):
    await bot.add_cog(Documents(bot))
