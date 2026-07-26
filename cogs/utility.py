import asyncio
import base64
import hashlib
import time
import platform
from datetime import datetime

import discord
from discord.ext import commands


class UtilityCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._start_time = time.time()
        self._reminders: list[dict] = []

    # ── Ping ──────────────────────────────────────────────────────────────

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Check bot latency."""
        msg = await ctx.send("Pinging...")
        latency = round(self.bot.latency * 1000)
        await msg.edit(
            content=f"🏓 Pong! **{latency}ms** | API: **{latency}ms**"
        )

    # ── Uptime ────────────────────────────────────────────────────────────

    @commands.command(name="uptime")
    async def uptime(self, ctx: commands.Context) -> None:
        """Show bot uptime."""
        elapsed = time.time() - self._start_time
        days, rem = divmod(int(elapsed), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        await ctx.send(f"⏱️ Uptime: **{' '.join(parts)}**")

    # ── Server Count ──────────────────────────────────────────────────────

    @commands.command(name="servercount", aliases=["guildcount", "servers"])
    async def servercount(self, ctx: commands.Context) -> None:
        """Show number of guilds the bot is in."""
        count = len(self.bot.guilds)
        await ctx.send(f"🏰 I'm in **{count}** guild{'s' if count != 1 else ''}.")

    # ── Say ───────────────────────────────────────────────────────────────

    @commands.command(name="say")
    async def say(self, ctx: commands.Context, *, text: str) -> None:
        """Repeat your message."""
        await ctx.send(text)

    # ── Echo ──────────────────────────────────────────────────────────────

    @commands.command(name="echo")
    async def echo(self, ctx: commands.Context, *, text: str) -> None:
        """Send text to a channel."""
        await ctx.send(text)

    # ── Calculator ────────────────────────────────────────────────────────

    @commands.command(name="calc", aliases=["calculate", "math"])
    async def calc(self, ctx: commands.Context, *, expression: str) -> None:
        """Evaluate a math expression. Usage: calc 2+2*3"""
        allowed = set("0123456789+-*/.() ")
        if not all(c in allowed for c in expression):
            await ctx.send("❌ Only math characters allowed.")
            return
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            await ctx.send(f"🧮 `{expression}` = **{result}**")
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    # ── Base64 Encode / Decode ────────────────────────────────────────────

    @commands.command(name="b64encode", aliases=["b64e", "encode64"])
    async def b64encode(self, ctx: commands.Context, *, text: str) -> None:
        """Encode text to Base64."""
        encoded = base64.b64encode(text.encode()).decode()
        await ctx.send(f"```{encoded}```")

    @commands.command(name="b64decode", aliases=["b64d", "decode64"])
    async def b64decode(self, ctx: commands.Context, *, text: str) -> None:
        """Decode Base64 to text."""
        try:
            decoded = base64.b64decode(text.encode()).decode()
            await ctx.send(f"```{decoded}```")
        except Exception as e:
            await ctx.send(f"❌ Invalid Base64: `{e}`")

    # ── Character / Word Count ────────────────────────────────────────────

    @commands.command(name="charcount", aliases=["cc", "chars"])
    async def charcount(self, ctx: commands.Context, *, text: str) -> None:
        """Count characters and words."""
        chars = len(text)
        words = len(text.split())
        lines = text.count("\n") + 1
        await ctx.send(
            f"📝 **{chars}** characters, **{words}** words, **{lines}** lines"
        )

    # ── Reverse ───────────────────────────────────────────────────────────

    @commands.command(name="strreverse", aliases=["srev"])
    async def strreverse(self, ctx: commands.Context, *, text: str) -> None:
        """Reverse a string."""
        await ctx.send(f"🔄 `{text[::-1]}`")

    # ── Remind ────────────────────────────────────────────────────────────

    @commands.command(name="remind", aliases=["reminder", "timer"])
    async def remind(
        self, ctx: commands.Context, time_str: str, *, text: str
    ) -> None:
        """Set a reminder. Usage: remind 5m Check oven"""
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        if time_str[-1] not in multipliers:
            await ctx.send(
                "❌ Use format: `5s`, `10m`, `2h`, `1d` (s=seconds, m=minutes, h=hours, d=days)"
            )
            return
        try:
            amount = int(time_str[:-1])
            seconds = amount * multipliers[time_str[-1]]
        except ValueError:
            await ctx.send("❌ Invalid time value.")
            return

        await ctx.send(
            f"⏰ Reminder set for **{time_str}**: {text}"
        )
        await asyncio.sleep(seconds)
        try:
            await ctx.send(
                f"⏰ **Reminder** ({time_str}): {ctx.author.mention} {text}"
            )
        except (discord.Forbidden, discord.NotFound):
            pass

    # ── Urban Dictionary ──────────────────────────────────────────────────

    @commands.command(name="define", aliases=["dict", "definition"])
    async def define(self, ctx: commands.Context, *, word: str) -> None:
        """Look up a word definition (Urban Dictionary)."""
        import aiohttp

        url = f"https://api.urbandictionary.com/v0/define?term={word}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await ctx.send("❌ Failed to fetch definition.")
                    return
                data = await resp.json()

        defs = data.get("list", [])
        if not defs:
            await ctx.send(f"❌ No definition found for **{word}**.")
            return

        d = defs[0]
        definition = d.get("definition", "N/A")[:500]
        example = d.get("example", "")[:300]
        lines = [f"📖 **{word}**", "", definition]
        if example:
            lines.extend(["", f"*Example:* {example}"])
        await ctx.send("\n".join(lines))

    # ── Info ──────────────────────────────────────────────────────────────

    @commands.command(name="botinfo", aliases=["info", "about"])
    async def botinfo(self, ctx: commands.Context) -> None:
        """Show bot information."""
        embed = discord.Embed(
            title="🤖 ZNE Bot Info",
            color=discord.Color.from_rgb(99, 102, 241),
        )
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Commands", value=str(len(self.bot.commands)), inline=True)
        embed.add_field(
            name="Python",
            value=platform.python_version(),
            inline=True,
        )
        embed.add_field(
            name="discord.py",
            value=discord.__version__,
            inline=True,
        )
        elapsed = time.time() - self._start_time
        days, rem = divmod(int(elapsed), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        uptime_str = f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
        embed.add_field(name="Uptime", value=uptime_str, inline=True)
        embed.set_footer(text="ZNE Selfbot")
        await ctx.send(embed=embed)

    # ── Afk ───────────────────────────────────────────────────────────────

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK") -> None:
        """Set yourself as AFK."""
        try:
            await ctx.author.edit(nick=f"[AFK] {ctx.author.name[:27]}")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await ctx.send(f"💤 You are now AFK: **{reason}**")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityCommands(bot))
