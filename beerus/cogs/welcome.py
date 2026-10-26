"""Welcome embeds, join/leave logs, returning-member + rejoin detection, anti-raid, new-account alerts."""
from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands

from .. import persona
from ..util import age_text, duration_text, utcnow

log = logging.getLogger("beerus.welcome")
PURPLE, GREEN, GREY, ORANGE = 0x7B2FBE, 0x22C55E, 0x6B7280, 0xF59E0B


class Welcome(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    # ────────────────────────── join ──────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        bot, cfg = self.bot, self.bot.cfg
        try:
            rec = await bot.db.record_join(member.guild.id, member.id)
            now = time.time()
            age_days = (utcnow() - member.created_at).days
            rapid = bool(rec["returning"] and rec["last_leave"]
                         and now - rec["last_leave"] < float(cfg.get("raid.rapid_rejoin_seconds", 600)))
            await self._log_join(member, rec, age_days, rapid)
            await self._raid_check(member, now)
            if member.bot:
                return
            await self._send_welcome(member, rec, rapid)
        except Exception:
            log.exception("Join handling failed for %s", member.id)

    async def _log_join(self, member: discord.Member, rec: dict, age_days: int, rapid: bool) -> None:
        tags = []
        if member.bot:
            tags.append("🤖 BOT")
        if rec["returning"]:
            tags.append(f"🔁 returning (join #{rec['join_count']})")
        if rapid:
            tags.append("⚡ rapid rejoin")
        emb = discord.Embed(title="📥 Member joined", color=GREEN, timestamp=utcnow(),
                            description=f"{member.mention} `{member.id}`" + (f"\n{' · '.join(tags)}" if tags else ""))
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.add_field(name="Account age", value=f"{age_text(member.created_at)}\n<t:{int(member.created_at.timestamp())}:R>")
        emb.add_field(name="Members", value=f"{member.guild.member_count:,}")
        self.bot.logs.put(embed=emb)

        limit = int(self.bot.cfg.get("raid.new_account_days", 7))
        if age_days < limit and not member.bot:
            alert = discord.Embed(
                title="🆕 New-account alert", color=ORANGE, timestamp=utcnow(),
                description=f"{member.mention} is only **{age_days} day(s)** old (limit: {limit}). Keep an eye on them.")
            alert.set_thumbnail(url=member.display_avatar.url)
            self.bot.logs.put(embed=alert)

    async def _raid_check(self, member: discord.Member, now: float) -> None:
        cfg, gid = self.bot.cfg, member.guild.id
        window = float(cfg.get("raid.join_window_seconds", 60))
        times = [t for t in self.bot.join_times.get(gid, []) if now - t <= window] + [now]
        self.bot.join_times[gid] = times
        if len(times) >= int(cfg.get("raid.join_threshold", 6)) and \
                now - self.bot.last_raid_alert.get(gid, 0) > float(cfg.get("raid.alert_cooldown_seconds", 300)):
            self.bot.last_raid_alert[gid] = now
            emb = discord.Embed(
                title="🚨 Possible raid", color=0xDC2626, timestamp=utcnow(),
                description=f"**{len(times)}** members joined in the last {window:.0f}s.\n"
                            "Beerus only warns — mods, please review and act manually.")
            mods = " ".join(f"<@&{r}>" for r in cfg.get("exempt.mod_role_ids", []) or [])
            self.bot.logs.put(content=mods or None, embed=emb, allowed_mentions=discord.AllowedMentions(roles=True))

    async def _send_welcome(self, member: discord.Member, rec: dict, rapid: bool) -> None:
        cfg, guild = self.bot.cfg, member.guild
        cid = int(cfg.get("channels.welcome", 0) or 0)
        channel = guild.get_channel(cid)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(cid)
            except discord.HTTPException as exc:
                log.warning("Welcome channel unavailable: %s", exc)
                return

        name = member.display_name
        pick = persona.picker.pick
        if rapid:
            title = "Oye, hun hi ta gaya si! ⚡"
            text = pick("rapid", persona.RAPID_REJOIN_TEXT).format(name=name)
            kicker = "Wapas aa gaya"
        elif rec["returning"]:
            title = pick("ret_t", persona.RETURN_TITLES).format(name=name)
            text = pick("ret_x", persona.RETURN_TEXT)
            kicker = "Wapsi • Welcome back"
        else:
            title = pick("wel_t", persona.WELCOME_TITLES).format(name=name)
            text = pick("wel_x", persona.WELCOME_TEXT)
            kicker = "Naya Mehmaan"

        created = int(member.created_at.timestamp())
        count = guild.member_count or len(guild.members)
        emb = discord.Embed(title=f"🌌 {title}", description=f"{member.mention}\n\n{text}",
                            color=PURPLE, timestamp=utcnow())
        emb.set_author(name=f"Universe 7  •  {kicker}",
                       icon_url=guild.icon.url if guild.icon else self.bot.user.display_avatar.url)
        emb.set_thumbnail(url=member.display_avatar.replace(size=256).url)
        emb.add_field(name="👤 Naam", value=f"**{member.display_name}**\n`@{member.name}`", inline=True)
        emb.add_field(name="🔢 Member", value=f"**#{count:,}**\nhun asi {count:,} bande ne", inline=True)
        emb.add_field(name="🎂 Account Banaya",
                      value=f"<t:{created}:D>  (<t:{created}:R>)\n⏳ {age_text(member.created_at)}", inline=False)
        if rec["returning"]:
            emb.add_field(name="🔁 Wapsi", value=f"Eh tuhada **{rec['join_count']}** vaan chakkar ae", inline=True)
        rules = int(cfg.get("channels.rules", 0) or 0)
        if rules:
            emb.add_field(name="📜 Pehla kamm", value=f"<#{rules}> padho, fer maza karo!", inline=True)
        emb.set_footer(text=pick("wel_f", persona.WELCOME_FOOTER),
                       icon_url=self.bot.user.display_avatar.url)
        try:
            await channel.send(content=member.mention, embed=emb,
                               allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.Forbidden:
            log.warning("No permission to post in the welcome channel (need Send Messages + Embed Links).")
        except discord.HTTPException as exc:
            log.warning("Welcome failed: %s", exc)

    # ────────────────────────── leave ──────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        try:
            rec = await self.bot.db.record_leave(member.guild.id, member.id)
            if not self.bot.cfg.get("logging.join_leave", True):
                return
            stayed = duration_text(time.time() - member.joined_at.timestamp()) if member.joined_at else "?"
            emb = discord.Embed(title="📤 Member left", color=GREY, timestamp=utcnow(),
                                description=f"{member.mention} `{member.id}`" + (" 🤖 BOT" if member.bot else ""))
            emb.set_thumbnail(url=member.display_avatar.url)
            emb.add_field(name="Stayed", value=stayed)
            emb.add_field(name="Members", value=f"{member.guild.member_count:,}")
            if rec:
                emb.add_field(name="Joins", value=str(rec["join_count"]))
            self.bot.logs.put(embed=emb)
        except Exception:
            log.exception("Leave handling failed")


async def setup(bot) -> None:
    await bot.add_cog(Welcome(bot))
