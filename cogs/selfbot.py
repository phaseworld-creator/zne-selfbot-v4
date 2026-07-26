import asyncio
import time

import discord
from discord.ext import commands


class SelfbotCommands(commands.Cog):
    """Commands specific to selfbot mode."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Token Info (safe) ─────────────────────────────────────────────────

    @commands.command(name="myid")
    async def myid(self, ctx: commands.Context) -> None:
        """Show your user ID."""
        await ctx.send(f"🆔 Your ID: `{ctx.author.id}`")

    # ── Bulk Delete ───────────────────────────────────────────────────────

    @commands.command(name="purgebot", aliases=["pb"])
    async def purgebot(self, ctx: commands.Context, limit: int = 50) -> None:
        """Delete only your messages in this channel."""
        deleted = 0
        async for message in ctx.channel.history(limit=limit + 1):
            if message.author == self.bot.user:
                try:
                    await message.delete()
                    deleted += 1
                except (discord.Forbidden, discord.NotFound):
                    pass
                await asyncio.sleep(0.3)
        await ctx.send(f"🗑️ Deleted **{deleted}** messages.", delete_after=3)

    # ── Mass React ────────────────────────────────────────────────────────

    @commands.command(name="massreact")
    async def massreact(self, ctx: commands.Context, emoji: str, limit: int = 10) -> None:
        """React to the last N messages with an emoji."""
        count = 0
        async for message in ctx.channel.history(limit=limit):
            if message.author != self.bot.user:
                try:
                    await message.add_reaction(emoji)
                    count += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await asyncio.sleep(0.5)
        await ctx.send(f"✅ Reacted to **{count}** messages.", delete_after=3)

    # ── Ghost Ping ────────────────────────────────────────────────────────

    @commands.command(name="ghostping", aliases=["gp"])
    async def ghostping(self, ctx: commands.Context, member: discord.Member) -> None:
        """Ghost ping someone (ping then delete)."""
        msg = await ctx.send(f"{member.mention}")
        await asyncio.sleep(0.5)
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

    # ── Status Text ───────────────────────────────────────────────────────

    @commands.command(name="setstatus")
    async def setstatus(self, ctx: commands.Context, status: str, *, text: str = "") -> None:
        """Set your status. Usage: setstatus online/dnd/idle/invisible [text]"""
        status_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        s = status_map.get(status.lower())
        if s is None:
            await ctx.send("❌ Valid statuses: `online`, `idle`, `dnd`, `invisible`")
            return
        activity = discord.Activity(type=discord.ActivityType.playing, name=text) if text else None
        await self.bot.change_presence(status=s, activity=activity)
        await ctx.send(f"✅ Status set to **{status}**.", delete_after=3)

    # ── Set Activity ──────────────────────────────────────────────────────

    @commands.command(name="setactivity", aliases=["sa"])
    async def setactivity(
        self,
        ctx: commands.Context,
        activity_type: str,
        *,
        text: str = "",
    ) -> None:
        """Set your activity. Usage: setactivity playing/watching/listening/competing <text>"""
        type_map = {
            "playing": discord.ActivityType.playing,
            "watching": discord.ActivityType.watching,
            "listening": discord.ActivityType.listening,
            "competing": discord.ActivityType.competing,
        }
        atype = type_map.get(activity_type.lower())
        if atype is None:
            await ctx.send("❌ Types: `playing`, `watching`, `listening`, `competing`")
            return
        activity = discord.Activity(type=atype, name=text) if text else None
        await self.bot.change_presence(activity=activity)
        await ctx.send(f"✅ Activity set to **{activity_type}** {text}", delete_after=3)

    # ── Clear Activity ────────────────────────────────────────────────────

    @commands.command(name="clearactivity", aliases=["ca"])
    async def clearactivity(self, ctx: commands.Context) -> None:
        """Clear your current activity."""
        await self.bot.change_presence(activity=None)
        await ctx.send("✅ Activity cleared.", delete_after=3)

    # ── Nickname ──────────────────────────────────────────────────────────

    @commands.command(name="setnick", aliases=["sn"])
    async def setnick(self, ctx: commands.Context, *, nickname: str) -> None:
        """Set your nickname in the current server."""
        try:
            await ctx.author.edit(nick=nickname)
            await ctx.send(f"✅ Nickname set to **{nickname}**", delete_after=3)
        except (discord.Forbidden, discord.HTTPException) as e:
            await ctx.send(f"❌ Failed: {e}")

    # ── Server Info Detailed ──────────────────────────────────────────────

    @commands.command(name="serveragedetail", aliases=["sgd"])
    async def serveragedetail(self, ctx: commands.Context) -> None:
        """Detailed server age information."""
        if not ctx.guild:
            await ctx.send("❌ This command must be used in a server.")
            return
        created = ctx.guild.created_at
        now = discord.utils.utcnow()
        delta = now - created
        days = delta.days
        years, days = divmod(days, 365)
        months, days = divmod(days, 30)
        lines = [
            f"📅 **{ctx.guild.name}**",
            f"Created: {created.strftime('%B %d, %Y')}",
            f"Age: **{years}**y **{months}**m **{days}**d",
        ]
        await ctx.send("\n".join(lines))

    # ── Emoji Steal ───────────────────────────────────────────────────────

    @commands.command(name="emojilist", aliases=["el"])
    async def emojilist(self, ctx: commands.Context) -> None:
        """List all custom emojis in this server."""
        if not ctx.guild or not ctx.guild.emojis:
            await ctx.send("❌ No custom emojis found.")
            return
        emojis = [str(e) for e in ctx.guild.emojis]
        await ctx.send(f"🎨 **{len(emojis)}** emojis:\n{' '.join(emojis)}")

    # ── Role List ─────────────────────────────────────────────────────────

    @commands.command(name="rolelist", aliases=["rl"])
    async def rolelist(self, ctx: commands.Context) -> None:
        """List all roles in this server."""
        if not ctx.guild:
            await ctx.send("❌ This command must be used in a server.")
            return
        roles = sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True)
        role_lines = [f"{r.mention} ({r.members.__len__()} members)" for r in roles if r.name != "@everyone"]
        if not role_lines:
            await ctx.send("❌ No roles found.")
            return
        await ctx.send("\n".join(role_lines[:30]))

    # ── Channel List ──────────────────────────────────────────────────────

    @commands.command(name="channellist", aliases=["cl"])
    async def channellist(self, ctx: commands.Context) -> None:
        """List all channels in this server."""
        if not ctx.guild:
            await ctx.send("❌ This command must be used in a server.")
            return
        categories = {}
        for ch in ctx.guild.channels:
            cat = ch.category.name if ch.category else "No Category"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(ch)
        lines = []
        for cat_name, channels in categories.items():
            lines.append(f"**{cat_name}**")
            for ch in channels:
                icon = "#" if ch.type == discord.ChannelType.text else "🔊" if ch.type == discord.ChannelType.voice else "📁"
                lines.append(f"  {icon} {ch.name}")
        await ctx.send("\n".join(lines[:40]))

    # ── Snowflake Info ────────────────────────────────────────────────────

    @commands.command(name="snowflake", aliases=["sf"])
    async def snowflake(self, ctx: commands.Context, id_str: str) -> None:
        """Get info from a Discord ID (snowflake)."""
        try:
            snowflake_id = int(id_str)
        except ValueError:
            await ctx.send("❌ Invalid ID.")
            return
        # Discord epoch: 2015-01-01T00:00:00Z
        timestamp = ((snowflake_id >> 22) + 1420070400000) / 1000
        dt = discord.utils.utcnow()
        from datetime import datetime as _dt
        dt = _dt.fromtimestamp(timestamp, tz=__import__("datetime").timezone.utc)
        await ctx.send(f"❄️ ID `{snowflake_id}`\nCreated: **{dt.strftime('%B %d, %Y %H:%M:%S UTC')}**")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfbotCommands(bot))
