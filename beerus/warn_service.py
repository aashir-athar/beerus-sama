"""Warning service. WARN ONLY — this module never kicks, mutes, times out or bans anyone."""
from __future__ import annotations

import logging
import time

import discord

from . import persona
from .moderation.models import MsgInfo, Verdict, Violation
from .util import trunc, utcnow

log = logging.getLogger("beerus.warnings")
NO_PINGS = discord.AllowedMentions(everyone=False, roles=False, users=False, replied_user=True)


def progress_bar(active: int, slots: int) -> str:
    filled = min(active, slots)
    bar = "🟧" * filled + "⬜" * (slots - filled)
    return bar + (f" **+{active - slots}**" if active > slots else "")


class WarningService:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.last_warn: dict[tuple[int, int], float] = {}

    # ── main entry: called for every non-exempt message ──────
    async def process(self, message: discord.Message, info: MsgInfo, verdict: Verdict) -> bool:
        primary = verdict.primary
        if primary is None:
            return False
        cfg = self.bot.cfg
        key = (info.guild_id, info.author_id)
        cooldown = float(cfg.get("moderation.warn_cooldown_seconds", 20))
        if info.ts - self.last_warn.get(key, 0) < cooldown:
            if primary.category == "fight" and self.bot.engine.fight_notice_due(info):
                await self._channel_notice(message.channel, persona.picker.pick("fight_notice", persona.FIGHT_NOTICES))
            await self._log_suppressed(message, primary)
            return True            # handled (silently) — don't also run AI chat on a spam message
        self.last_warn[key] = info.ts

        member = message.author
        await self.issue(member=member, channel=message.channel, target=message, category=primary.category,
                         severity=primary.severity, score=max(verdict.score, primary.score),
                         source=primary.source, detail=primary.detail, verdict=verdict)
        if cfg.get("moderation.delete_offending_messages", False):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        return True

    # ── also used by /beerus warn ────────────────────────────
    async def issue(self, *, member: discord.Member, channel, target: discord.Message | None, category: str,
                    severity: int, score: int, source: str, detail: str = "",
                    verdict: Verdict | None = None, issued_by: int | None = None, manual_reason: str = "") -> int:
        bot, cfg = self.bot, self.bot.cfg
        guild = member.guild
        label = persona.CATEGORY_LABEL.get(category, "Zehreeli gall")
        reason = manual_reason or label
        await bot.db.add_warning(guild.id, member.id, category, severity, reason, score, channel.id, issued_by)
        active = await bot.db.active_warning_count(guild.id, member.id, float(cfg.get("moderation.warning_expiry_days", 30)))
        total = await bot.db.total_warning_count(guild.id, member.id)
        bot.heat.bump(guild.id, severity)
        mood = persona.mood_for_user(active, bot.heat.get(guild.id))

        text = persona.warning_text(mood, category, f"**{member.display_name}**", active)
        slots = int(cfg.get("moderation.display_warning_slots", 5))
        embed = discord.Embed(description=text, color=persona.MOOD_COLOR[mood], timestamp=utcnow())
        embed.set_author(name=f"⚠️ Warning #{active}  •  {member.display_name}", icon_url=member.display_avatar.url)
        embed.add_field(name="Wajah", value=f"{persona.CATEGORY_ICON.get(category, '⚠️')} {reason}", inline=True)
        embed.add_field(name="Beerus da mood", value=persona.MOOD_LABEL[mood], inline=True)
        embed.add_field(name="Warnings", value=progress_bar(active, slots), inline=False)
        embed.set_footer(text="Beerus-Sama • Universe 7", icon_url=bot.user.display_avatar.url if bot.user else None)

        try:
            if target is not None:
                await target.reply(embed=embed, mention_author=True, allowed_mentions=NO_PINGS)
            else:
                await channel.send(content=member.mention, embed=embed,
                                   allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            try:       # original message deleted → plain send
                await channel.send(content=member.mention, embed=embed,
                                   allowed_mentions=discord.AllowedMentions(users=[member]))
            except discord.HTTPException as exc:
                log.warning("Could not post warning: %s", exc)

        # ── mod log ──
        log_embed = discord.Embed(title=f"{persona.CATEGORY_ICON.get(category, '⚠️')} Warning #{active} — {label}",
                                  color=persona.MOOD_COLOR[mood], timestamp=utcnow())
        log_embed.set_thumbnail(url=member.display_avatar.url)
        log_embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        log_embed.add_field(name="Channel", value=getattr(channel, "mention", "?"), inline=True)
        log_embed.add_field(name="Score / Source", value=f"`{score}/100` · {source}", inline=True)
        if verdict:
            log_embed.add_field(name="Language", value=verdict.language.replace("_", " "), inline=True)
        log_embed.add_field(name="Active / Total", value=f"{active} / {total}", inline=True)
        if detail:
            log_embed.add_field(name="Detail", value=trunc(detail, 200), inline=True)
        if verdict and verdict.notes:
            log_embed.add_field(name="Notes", value=trunc(" • ".join(verdict.notes), 300), inline=False)
        if target is not None:
            excerpt = trunc(target.clean_content, int(cfg.get("logging.max_excerpt_chars", 180)))
            log_embed.add_field(name="Message", value=f"||{excerpt or '(no text)'}||\n[Jump]({target.jump_url})", inline=False)
        bot.logs.put(embed=log_embed)

        threshold = int(cfg.get("moderation.escalate_to_mods_at", 5))
        if active >= threshold and active % threshold == 0:
            alert = discord.Embed(
                title="🚨 Manual action recommended",
                description=(f"{member.mention} has **{active}** active warnings.\n"
                             "Beerus only warns — kick / mute / ban is for the human mods to decide."),
                color=0xDC2626, timestamp=utcnow())
            mods = " ".join(f"<@&{r}>" for r in cfg.get("exempt.mod_role_ids", []) or [])
            bot.logs.put(content=mods or None, embed=alert,
                         allowed_mentions=discord.AllowedMentions(roles=True))
        return active

    async def _channel_notice(self, channel, text: str) -> None:
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def _log_suppressed(self, message: discord.Message, primary: Violation) -> None:
        log.debug("Warning suppressed by cooldown: user=%s cat=%s", message.author.id, primary.category)
