"""Server activity logs: message edits/deletes, channel changes, bans/unbans done by humans (observe only)."""
from __future__ import annotations

import discord
from discord.ext import commands

from ..util import trunc, utcnow


class Events(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    def _skip(self, message: discord.Message) -> bool:
        return (message.author.bot or message.guild is None
                or message.channel.id == int(self.bot.cfg.get("channels.logs", 0) or 0))

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if self._skip(message) or not self.bot.cfg.get("logging.message_delete", True):
            return
        emb = discord.Embed(title="🗑️ Message deleted", color=0x6B7280, timestamp=utcnow(),
                            description=f"{message.author.mention} in {message.channel.mention}")
        emb.add_field(name="Content", value=f"||{trunc(message.clean_content, 500) or '(attachment / embed)'}||", inline=False)
        if message.attachments:
            emb.add_field(name="Attachments", value=str(len(message.attachments)))
        self.bot.logs.put(embed=emb)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if self._skip(after) or before.content == after.content or not self.bot.cfg.get("logging.message_edit", True):
            return
        emb = discord.Embed(title="✏️ Message edited", color=0x3B82F6, timestamp=utcnow(),
                            description=f"{after.author.mention} in {after.channel.mention} · [Jump]({after.jump_url})")
        emb.add_field(name="Before", value=f"||{trunc(before.clean_content, 400) or '—'}||", inline=False)
        emb.add_field(name="After", value=f"||{trunc(after.clean_content, 400) or '—'}||", inline=False)
        self.bot.logs.put(embed=emb)
        # edited messages are moderated too (people edit abuse in after posting)
        if self.bot.is_exempt(after) is False and after.type in (discord.MessageType.default, discord.MessageType.reply):
            info = await self.bot.build_info(after)
            verdict = await self.bot.engine.evaluate(info, privileged=info.author_is_admin)
            text_only = [v for v in verdict.violations if v.source not in ("spam",)]
            if text_only:
                verdict.violations = text_only
                await self.bot.warnings.process(after, info, verdict)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel) -> None:
        if self.bot.cfg.get("logging.channels", True):
            self.bot.logs.put(embed=discord.Embed(title="📁 Channel created", description=f"{channel.mention} (`{channel.name}`)",
                                                  color=0x22C55E, timestamp=utcnow()))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel) -> None:
        if self.bot.cfg.get("logging.channels", True):
            self.bot.logs.put(embed=discord.Embed(title="🗂️ Channel deleted", description=f"`#{channel.name}`",
                                                  color=0xDC2626, timestamp=utcnow()))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user) -> None:   # done by a human mod — we only record it
        self.bot.logs.put(embed=discord.Embed(title="🔨 Member banned (by a moderator)", description=f"{user} `{user.id}`",
                                              color=0xDC2626, timestamp=utcnow()))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user) -> None:
        self.bot.logs.put(embed=discord.Embed(title="🕊️ Member unbanned", description=f"{user} `{user.id}`",
                                              color=0x22C55E, timestamp=utcnow()))


async def setup(bot) -> None:
    await bot.add_cog(Events(bot))
