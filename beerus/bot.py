"""Beerus-Sama — the bot itself. WARN ONLY: no kick / mute / timeout / ban code exists in this project."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import discord
from discord.ext import commands, tasks

from . import persona
from .ai import ChatService, GroqClient
from .backup import BackupManager
from .config import DATA_DIR, Config
from .db import Database
from .moderation.engine import ModerationEngine
from .moderation.models import MsgInfo
from .util import trunc, utcnow
from .warn_service import WarningService

log = logging.getLogger("beerus")
EXTENSIONS = ("beerus.cogs.welcome", "beerus.cogs.events", "beerus.cogs.admin")


class LogSender:
    """Rate-limited queue → logs channel. Never raises into callers."""

    def __init__(self, bot: "Beerus") -> None:
        self.bot = bot
        self.q: asyncio.Queue = asyncio.Queue(maxsize=300)
        self.task: asyncio.Task | None = None
        self.channel = None
        self.log = logging.getLogger("beerus.logsender")

    def start(self) -> None:
        self.task = asyncio.create_task(self._worker())

    def put(self, **kwargs) -> None:
        try:
            self.q.put_nowait(kwargs)
        except asyncio.QueueFull:
            pass

    async def _resolve(self):
        cid = int(self.bot.cfg.get("channels.logs", 0) or 0)
        if not cid:
            return None
        if self.channel is None or self.channel.id != cid:
            self.channel = self.bot.get_channel(cid)
            if self.channel is None:
                try:
                    self.channel = await self.bot.fetch_channel(cid)
                except discord.HTTPException as exc:
                    self.log.warning("Logs channel %s unavailable: %s", cid, exc)
        return self.channel

    async def _worker(self) -> None:
        while True:
            item = await self.q.get()
            try:
                ch = await self._resolve()
                if ch:
                    item.setdefault("allowed_mentions", discord.AllowedMentions.none())
                    await ch.send(**item)
            except discord.HTTPException as exc:
                self.log.warning("Could not write log: %s", exc)
            except Exception as exc:                      # noqa: BLE001 — logger must never die
                self.log.warning("Log worker error: %r", exc)
            await asyncio.sleep(0.7)

    async def flush(self, timeout: float = 8.0) -> None:
        end = time.time() + timeout
        while not self.q.empty() and time.time() < end:
            await asyncio.sleep(0.2)


class DiscordErrorHandler(logging.Handler):
    """Forwards ERROR+ log records to the logs channel (deduplicated, max ~6/min)."""

    def __init__(self, bot: "Beerus") -> None:
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.seen: dict[str, float] = {}
        self.stamps: list[float] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("beerus.logsender") or not getattr(self.bot, "logs", None):
            return
        try:
            msg = self.format(record)
            now = time.time()
            key = msg[:120]
            self.stamps = [t for t in self.stamps if now - t < 60]
            if now - self.seen.get(key, 0) < 60 or len(self.stamps) >= 6:
                return
            self.seen[key] = now
            self.stamps.append(now)
            emb = discord.Embed(title="🛑 Error", description=f"```py\n{trunc(msg, 1800)}\n```",
                                color=0xDC2626, timestamp=utcnow())
            emb.set_footer(text=record.name)
            self.bot.logs.put(embed=emb)
        except Exception:                                   # noqa: BLE001
            pass


class Beerus(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True      # enable in the Developer Portal too
        intents.members = True              # enable in the Developer Portal too
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.watching, name="Universe 7 | Whis nu pudding de"),
            help_command=None,
            max_messages=2000,
        )
        self.cfg = Config()
        self.db = Database(DATA_DIR / "beerus.db")
        self.heat = persona.HeatMeter()
        self.groq = GroqClient(self.cfg)
        self.engine = ModerationEngine(self.cfg, self.groq)
        self.chat = ChatService(self.groq, self.cfg, self.engine.lexicon, self.heat)
        self.warnings = WarningService(self)
        self.logs = LogSender(self)
        self.backup: BackupManager | None = None
        self.started_at = time.time()
        self.join_times: dict[int, list[float]] = {}
        self.last_raid_alert: dict[int, float] = {}
        self._error_handler = DiscordErrorHandler(self)
        logging.getLogger().addHandler(self._error_handler)

    # ───────────────────────── startup ─────────────────────────
    async def setup_hook(self) -> None:
        self.backup = BackupManager(self)
        if not self.db.exists():
            log.info("No local database — trying to restore from the logs channel…")
            await self.backup.restore_latest()
        self.db.open()
        for key, val in (await self.db.all_settings("cfg:")).items():
            self.cfg.set_override(key[4:], val)
        self.logs.start()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        self.tree.on_error = self.on_app_command_error
        await self._sync_commands()
        self.maintenance.start()

    async def _sync_commands(self) -> None:
        try:
            synced = await self.tree.sync()
        except discord.HTTPException:
            log.exception("Slash command sync failed; aborting startup.")
            raise
        log.info("Slash commands synced: %s", ", ".join(command.name for command in synced) or "(none)")

    async def on_ready(self) -> None:
        log.info("Beerus-Sama is awake as %s (guilds: %d) — AI: %s",
                 self.user, len(self.guilds), "on" if self.groq.configured else "off (no GROQ_API_KEY)")
        emb = discord.Embed(
            title="🟣 Beerus-Sama jaag gaya",
            description=f"Latency `{self.latency * 1000:.0f} ms` · Guilds `{len(self.guilds)}` · "
                        f"AI `{'on' if self.groq.configured else 'off'}` · Model `{self.groq.model}`",
            color=persona.MOOD_COLOR["calm"], timestamp=utcnow())
        self.logs.put(embed=emb)

    async def on_resumed(self) -> None:
        log.info("Gateway session resumed.")

    @tasks.loop(minutes=10)
    async def maintenance(self) -> None:
        """Automatic DB backup + housekeeping."""
        try:
            if self.backup and await self.backup.due():
                await self.backup.make("scheduled")
            now = time.time()
            for gid in list(self.last_raid_alert):
                if now - self.last_raid_alert[gid] > 3600:
                    self.last_raid_alert.pop(gid, None)
            self.warnings.last_warn = {k: v for k, v in self.warnings.last_warn.items() if now - v < 600}
        except Exception:
            log.exception("Maintenance task failed")

    @maintenance.before_loop
    async def _before_maintenance(self) -> None:
        await self.wait_until_ready()

    async def close(self) -> None:
        log.info("Shutting down…")
        try:
            if self.maintenance.is_running():
                self.maintenance.cancel()
            if self.backup and self.db._conn is not None and self.is_ready():
                await self.backup.make("shutdown")
            await self.logs.flush()
        except Exception:
            log.exception("Shutdown backup failed")
        await self.groq.close()
        self.db.close()
        logging.getLogger().removeHandler(self._error_handler)
        await super().close()

    # ───────────────────────── messages ─────────────────────────
    def is_exempt(self, message: discord.Message) -> bool:
        if message.channel.id in (self.cfg.get("exempt.channel_ids", []) or []):
            return True
        member = message.author
        if isinstance(member, discord.Member):
            ex_roles = set(self.cfg.get("exempt.role_ids", []) or [])
            if ex_roles & {r.id for r in member.roles}:
                return True
            if self.cfg.get("exempt.administrators", False) and member.guild_permissions.administrator:
                return True
        return False

    async def build_info(self, message: discord.Message, fetch_reply: bool = False) -> MsgInfo:
        ref_msg: discord.Message | None = None
        ref = message.reference
        if ref:
            resolved = ref.resolved
            if isinstance(resolved, discord.Message):
                ref_msg = resolved
            elif fetch_reply and ref.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(ref.message_id)
                except discord.HTTPException:
                    ref_msg = None
        author = message.author
        perms = author.guild_permissions if isinstance(author, discord.Member) else None
        return MsgInfo(
            guild_id=message.guild.id, channel_id=message.channel.id, message_id=message.id,
            author_id=author.id, author_name=author.display_name, content=message.content or "",
            ts=message.created_at.timestamp(),
            mention_count=len([u for u in message.mentions if u.id != author.id]),
            role_mention_count=len(message.role_mentions),
            mentions_everyone=("@everyone" in message.content or "@here" in message.content),
            reply_to_author_id=ref_msg.author.id if ref_msg else None,
            reply_to_is_bot=bool(ref_msg and ref_msg.author.id == self.user.id),
            reply_to_content=ref_msg.clean_content[:200] if ref_msg else "",
            attachment_count=len(message.attachments),
            account_age_days=(utcnow() - author.created_at).total_seconds() / 86400,
            author_is_admin=bool(perms and perms.mention_everyone),
            mentions_bot=any(u.id == self.user.id for u in message.mentions),
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or message.type not in (
                discord.MessageType.default, discord.MessageType.reply):
            return
        try:
            if self.is_exempt(message):
                return
            info = await self.build_info(message)
            verdict = await self.engine.evaluate(info, privileged=info.author_is_admin)
            if await self.warnings.process(message, info, verdict):
                return
            if info.mentions_bot and not message.mention_everyone:
                await self.handle_chat(message, info)
        except Exception:
            log.exception("on_message failed (message %s)", message.id)

    async def handle_chat(self, message: discord.Message, info: MsgInfo) -> None:
        if message.reference and not info.reply_to_content:
            info = await self.build_info(message, fetch_reply=True)
        if self.chat.on_cooldown(info.author_id):
            try:
                await message.add_reaction("⏳")
            except discord.HTTPException:
                pass
            return
        async with message.channel.typing():
            line = await self.chat.reply(info, message.content)
        try:
            await message.reply(line, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            try:
                await message.channel.send(line, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                log.warning("Could not send chat reply: %s", exc)

    # ───────────────────────── errors ─────────────────────────
    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        log.exception("Unhandled error in event %s", event_method)

    async def on_app_command_error(self, interaction: discord.Interaction, error) -> None:
        log.error("Slash command error: %r", error)
        msg = "Kujh gadbad ho gayi, Whis nu dassda haan. 😤"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass
