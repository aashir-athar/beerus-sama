"""Automatic database backups — local rotation + upload to the private logs channel (free, durable).

On a fresh machine (e.g. a new GitHub Actions runner) the newest backup in the logs channel
is downloaded automatically, so warnings survive restarts.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import discord

log = logging.getLogger("beerus.backup")
PREFIX = "beerus_backup_"


class BackupManager:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.dir = bot.db.path.parent / "backups"
        self.dir.mkdir(parents=True, exist_ok=True)

    async def _logs_channel(self):
        cid = int(self.bot.cfg.get("channels.logs", 0) or 0)
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except discord.HTTPException:
                return None
        return ch

    async def make(self, reason: str = "scheduled", upload: bool | None = None) -> Path | None:
        if not self.bot.cfg.get("backup.enabled", True):
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = self.dir / f"{PREFIX}{stamp}.db"
        try:
            await self.bot.db.backup_to(dest)
            await self.bot.db.set_setting("last_backup_ts", time.time())
        except Exception:
            log.exception("Backup failed")
            return None
        self._prune()
        if upload is None:
            upload = bool(self.bot.cfg.get("backup.upload_to_discord", True))
        if upload:
            ch = await self._logs_channel()
            if ch:
                try:
                    await ch.send(content=f"💾 **Database backup** ({reason}) — `{dest.stat().st_size // 1024} KB`",
                                  file=discord.File(dest, filename=dest.name))
                except discord.HTTPException as exc:
                    log.warning("Backup upload failed: %s", exc)
        log.info("Backup created: %s (%s)", dest.name, reason)
        return dest

    def _prune(self) -> None:
        keep = int(self.bot.cfg.get("backup.keep_local", 5))
        files = sorted(self.dir.glob(f"{PREFIX}*.db"))
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass

    async def due(self) -> bool:
        last = float(await self.bot.db.get_setting("last_backup_ts", 0) or 0)
        return time.time() - last >= float(self.bot.cfg.get("backup.interval_hours", 6)) * 3600

    async def restore_latest(self) -> bool:
        """Download the newest backup from the logs channel into data/beerus.db (only if no DB exists)."""
        ch = await self._logs_channel()
        if ch is None:
            return False
        try:
            async for msg in ch.history(limit=300):
                if msg.author.id != self.bot.user.id:
                    continue
                for att in msg.attachments:
                    if att.filename.startswith(PREFIX) and att.filename.endswith(".db"):
                        data = await att.read()
                        self.bot.db.path.write_bytes(data)
                        log.info("Database restored from Discord backup %s", att.filename)
                        return True
        except discord.HTTPException as exc:
            log.warning("Could not search for backups: %s", exc)
        return False
