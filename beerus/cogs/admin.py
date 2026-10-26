"""Admin slash commands (/beerus ...). Administrators only."""
from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from .. import persona
from ..moderation.models import MsgInfo
from ..util import duration_text, trunc, utcnow

PROTECTED_KEYS = ("moderation.warn_only", "ai.extra_params")


def coerce(old, new):
    if isinstance(old, bool):
        if isinstance(new, bool):
            return new
        raise ValueError("expected true/false")
    if isinstance(old, int):
        if isinstance(new, int) and not isinstance(new, bool):
            return new
        raise ValueError("expected a whole number")
    if isinstance(old, float):
        if isinstance(new, (int, float)) and not isinstance(new, bool):
            return float(new)
        raise ValueError("expected a number")
    if isinstance(old, list):
        if isinstance(new, list):
            return new
        raise ValueError('expected a JSON list, e.g. [1,2,3]')
    return str(new)


def admin_only():
    async def pred(interaction: discord.Interaction) -> bool:
        m = interaction.user
        if isinstance(m, discord.Member) and m.guild_permissions.administrator:
            return True
        raise app_commands.CheckFailure("Administrator only.")
    return app_commands.check(pred)


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class BeerusGroup(app_commands.Group):
    pass


class Admin(commands.Cog):
    beerus = BeerusGroup(name="beerus", description="Beerus-Sama admin controls")
    config = app_commands.Group(name="config", description="View / change settings", parent=beerus)

    def __init__(self, bot) -> None:
        self.bot = bot

    # ── status ──
    @beerus.command(name="status", description="Bot health, AI budget and uptime")
    @admin_only()
    async def status(self, interaction: discord.Interaction) -> None:
        b = self.bot
        emb = discord.Embed(title="🟣 Beerus-Sama status", color=persona.MOOD_COLOR["calm"], timestamp=utcnow())
        emb.add_field(name="Uptime", value=duration_text(time.time() - b.started_at))
        emb.add_field(name="Latency", value=f"{b.latency * 1000:.0f} ms")
        emb.add_field(name="Server mood", value=persona.MOOD_LABEL[b.heat.mood(interaction.guild_id)])
        emb.add_field(name="Groq", value=("✅ on" if b.groq.configured else "❌ off") + f"\n`{b.groq.model}`")
        emb.add_field(name="AI budget (today)", value=b.groq.budget.summary(), inline=False)
        if b.groq.last_error:
            emb.add_field(name="Last AI issue", value=b.groq.last_error)
        last = await b.db.get_setting("last_backup_ts", 0)
        emb.add_field(name="Last backup", value=f"<t:{int(last)}:R>" if last else "never")
        emb.add_field(name="Rule", value="⚠️ Warn-only. No kick / mute / ban.", inline=False)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ── warnings ──
    @beerus.command(name="warnings", description="Show a member's warning history")
    @admin_only()
    async def warnings(self, interaction: discord.Interaction, user: discord.Member) -> None:
        rows = await self.bot.db.recent_warnings(interaction.guild_id, user.id, 10)
        active = await self.bot.db.active_warning_count(
            interaction.guild_id, user.id, float(self.bot.cfg.get("moderation.warning_expiry_days", 30)))
        emb = discord.Embed(title=f"Warnings — {user.display_name}", color=0xF59E0B, timestamp=utcnow(),
                            description=f"Active: **{active}**" if rows else "Saaf suthra record. ✨")
        emb.set_thumbnail(url=user.display_avatar.url)
        for r in rows:
            who = "auto" if r["issued_by"] is None else f"<@{r['issued_by']}>"
            emb.add_field(name=f"{persona.CATEGORY_ICON.get(r['category'], '⚠️')} {trunc(r['reason'], 40)}",
                          value=f"<t:{int(r['created_at'])}:R> · score {r['score']} · {who}"
                                + ("" if r["active"] else " · cleared"), inline=False)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @beerus.command(name="clearwarnings", description="Clear a member's active warnings")
    @admin_only()
    async def clearwarnings(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await self.bot.db.clear_warnings(interaction.guild_id, user.id)
        self.bot.logs.put(embed=discord.Embed(
            title="🧹 Warnings cleared", description=f"{user.mention} by {interaction.user.mention}",
            color=0x22C55E, timestamp=utcnow()))
        await interaction.response.send_message(f"✅ {user.display_name} de warnings saaf kar ditte.", ephemeral=True)

    @beerus.command(name="warn", description="Give a manual warning (Beerus-style)")
    @app_commands.describe(user="Who", reason="Why (shown in the warning)")
    @admin_only()
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str) -> None:
        if user.bot:
            await interaction.response.send_message("Bots nu warning? Whis hasega. 😏", ephemeral=True)
            return
        await interaction.response.send_message("✅ Warning ditti ja rahi ae.", ephemeral=True)
        await self.bot.warnings.issue(
            member=user, channel=interaction.channel, target=None, category="toxicity", severity=1, score=0,
            source="moderator", issued_by=interaction.user.id, manual_reason=trunc(reason, 150))

    @beerus.command(name="top", description="Members with the most active warnings")
    @admin_only()
    async def top(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.top_offenders(
            interaction.guild_id, float(self.bot.cfg.get("moderation.warning_expiry_days", 30)))
        text = "\n".join(f"**{i + 1}.** <@{r['user_id']}> — {r['c']}" for i, r in enumerate(rows)) or "Koi nahi. Sab changey bache ne! 😌"
        await interaction.response.send_message(embed=discord.Embed(title="🏆 Top offenders", description=text, color=0xF59E0B),
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    # ── dry-run ──
    @beerus.command(name="test", description="Dry-run: see how Beerus would score a message (nothing is posted/logged)")
    @admin_only()
    async def test(self, interaction: discord.Interaction, text: str) -> None:
        info = MsgInfo(guild_id=interaction.guild_id, channel_id=interaction.channel_id, message_id=0,
                       author_id=interaction.user.id, author_name=interaction.user.display_name,
                       content=text, ts=time.time())
        r = self.bot.engine.text.analyze(info, self.bot.cfg)
        thr = int(self.bot.cfg.get("moderation.warn_threshold", 55))
        verdict = "🚨 **WARN**" if r.category and r.score >= thr else (
            "🤔 gray zone → AI review" if r.score >= int(self.bot.cfg.get("moderation.ai_review_range", [25, 55])[0]) else "✅ clean")
        emb = discord.Embed(title="🧪 Dry run", color=0x3B82F6, description=f"||{trunc(text, 300)}||")
        emb.add_field(name="Verdict", value=verdict)
        emb.add_field(name="Toxicity", value=f"{r.score}/100 (warn at {thr})")
        emb.add_field(name="Category", value=r.category or "—")
        emb.add_field(name="Language", value=r.language.replace("_", " "))
        emb.add_field(name="Signals", value=", ".join(
            x for x, on in (("targeted", r.targeted), ("banter", r.banter), ("talking-about-word", r.meta),
                            ("quoted", r.had_quote)) if on) or "—")
        emb.add_field(name="Matched", value=r.hit_summary() or "—")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ── backup / reload ──
    @beerus.command(name="backup", description="Make a database backup now")
    @admin_only()
    async def backup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        path = await self.bot.backup.make("manual")
        await interaction.followup.send(f"💾 Backup ready: `{path.name}`" if path else "❌ Backup failed — check logs.", ephemeral=True)

    @beerus.command(name="reload", description="Reload rules.yaml and lexicon.yaml")
    @admin_only()
    async def reload(self, interaction: discord.Interaction) -> None:
        try:
            self.bot.cfg.reload()
            self.bot.engine.reload()
            self.bot.chat.lex = self.bot.engine.lexicon
            await interaction.response.send_message("🔄 Rules te lexicon dobara load ho gaye.", ephemeral=True)
        except Exception as exc:                                 # noqa: BLE001
            await interaction.response.send_message(f"❌ Reload failed: `{exc}`", ephemeral=True)

    # ── config ──
    @config.command(name="view", description="List settings (optionally filter by prefix, e.g. spam.)")
    @admin_only()
    async def config_view(self, interaction: discord.Interaction, prefix: str = "") -> None:
        flat = {k: v for k, v in self.bot.cfg.flat().items() if k.startswith(prefix)}
        lines = [f"`{k}` = `{trunc(str(v), 60)}`" + (" ✏️" if k in self.bot.cfg.overrides else "") for k, v in sorted(flat.items())]
        text = "\n".join(lines) or "Kujh ni milya."
        await interaction.response.send_message(trunc(text, 1900) + "\n\n✏️ = changed from Discord", ephemeral=True)

    @config.command(name="set", description='Change a setting, e.g. key=spam.flood.max_messages value=8')
    @admin_only()
    async def config_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        cfg = self.bot.cfg
        if any(key.startswith(p) for p in PROTECTED_KEYS):
            await interaction.response.send_message("🔒 Eh setting badli ni ja sakdi (warn-only rule).", ephemeral=True)
            return
        if not cfg.has_path(key):
            await interaction.response.send_message("❌ Unknown key. Try `/beerus config view`.", ephemeral=True)
            return
        try:
            new = coerce(cfg.get(key), cfg.parse_value(value))
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        cfg.set_override(key, new)
        await self.bot.db.set_setting(f"cfg:{key}", new)
        await interaction.response.send_message(f"✅ `{key}` = `{new}`", ephemeral=True)

    @config.command(name="reset", description="Go back to the value in rules.yaml")
    @admin_only()
    async def config_reset(self, interaction: discord.Interaction, key: str) -> None:
        self.bot.cfg.clear_override(key)
        await self.bot.db.delete_setting(f"cfg:{key}")
        await interaction.response.send_message(f"↩️ `{key}` reset.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
