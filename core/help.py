import asyncio

import discord
from discord.ext import commands


def _clean_category(name: str) -> str:
    """Strip 'Commands' and 'Cog' suffixes from cog class names."""
    for suffix in ("Commands", "Cog"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name or "Uncategorized"


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_pages(self) -> list[dict]:
        """Build one page per cog (category), commands sorted alphabetically."""
        pages: list[dict] = []

        for cog in self.bot.cogs.values():
            category = _clean_category(type(cog).__name__)
            if category == "Help":
                continue

            visible = [cmd for cmd in cog.get_commands() if not cmd.hidden]
            if visible:
                pages.append(
                    {
                        "category_name": category,
                        "commands": sorted(
                            [
                                (c.name, c.usage or c.name, c.help or "")
                                for c in visible
                            ],
                            key=lambda x: x[0],
                        ),
                    }
                )

        # Collect uncategorized commands (not in any cog)
        uncategorized = [
            cmd
            for cmd in self.bot.commands
            if cmd.cog is None and not cmd.hidden
        ]
        if uncategorized:
            pages.append(
                {
                    "category_name": "Uncategorized",
                    "commands": sorted(
                        [
                            (c.name, c.usage or c.name, c.help or "")
                            for c in uncategorized
                        ],
                        key=lambda x: x[0],
                    ),
                }
            )

        if not pages:
            pages.append(
                {
                    "category_name": "No Commands",
                    "commands": [],
                }
            )

        return pages

    def _render_page(self, page: dict, page_num: int, total: int) -> str:
        """Render a single help page with page counter."""
        name = page["category_name"]
        cmds = page["commands"]

        if not cmds:
            return (
                f"## 📚 {name} ({page_num}/{total})\n"
                "No commands in this category."
            )

        lines = [
            f"## 📚 {name} ({page_num}/{total})",
            "",
        ]

        for cmd_name, _usage, desc in cmds:
            if desc:
                lines.append(f"**{cmd_name}** — {desc}")
            else:
                lines.append(f"**{cmd_name}**")

        lines.append("")
        lines.append(
            f"Type `{self.bot.command_prefix}help <command>` for usage details."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Command
    # ------------------------------------------------------------------

    @commands.command(name="help")
    async def help_command(
        self, ctx: commands.Context, *, command_name: str = None
    ) -> None:
        # ---------- single-command lookup ----------
        if command_name:
            try:
                cmd = self.bot.get_command(command_name)
            except Exception:
                await ctx.send(f"❌ Command `{command_name}` not found.")
                return

            if cmd is None:
                await ctx.send(f"❌ Command `{command_name}` not found.")
                return

            lines = [
                f"## 📖 {cmd.name}",
                "",
                f"**Usage:** `{ctx.prefix}{cmd.usage or cmd.name}`",
            ]
            if cmd.help:
                lines.append(f"**Description:** {cmd.help}")
            if cmd.aliases:
                lines.append(
                    f"**Aliases:** {', '.join(f'`{a}`' for a in cmd.aliases)}"
                )
            if cmd.cog:
                lines.append(
                    f"**Category:** {_clean_category(type(cmd.cog).__name__)}"
                )

            try:
                await ctx.send("\n".join(lines))
            except (discord.Forbidden, discord.NotFound):
                pass
            return

        # ---------- paginated overview ----------
        total_commands = len(self.bot.commands)
        total_categories = sum(
            1
            for cog in self.bot.cogs.values()
            if len(cog.get_commands()) > 0
        )

        pages = self._build_pages()
        total_pages = len(pages)
        current_page = 0

        # Build and send the welcome message
        welcome = (
            f"## Welcome! {ctx.author.mention}\n"
            f"Welcome to **ZNE Selfbot** — you have access to\n"
            f"**{total_commands}** commands across **{total_categories}** categories!\n\n"
            f"Type `next`, `prev`, or a page number to navigate. "
            f"Type `stop` to close."
        )

        try:
            await ctx.send(welcome)
        except (discord.Forbidden, discord.NotFound):
            return

        # Send the first page
        try:
            page_msg = await ctx.send(
                self._render_page(pages[current_page], current_page + 1, total_pages)
            )
        except (discord.Forbidden, discord.NotFound):
            return

        # ---------- navigation loop ----------
        while True:
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: (
                        m.author.id == ctx.author.id
                        and m.channel.id == ctx.channel.id
                    ),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                try:
                    await page_msg.edit(
                        content=(
                            self._render_page(
                                pages[current_page],
                                current_page + 1,
                                total_pages,
                            )
                            + "\n\n*Timed out — help closed.*"
                        )
                    )
                except (discord.Forbidden, discord.NotFound):
                    pass
                break

            # Try to delete the user's navigation message (best-effort)
            try:
                await msg.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

            text = msg.content.strip().lower()

            # Close help
            if text in ("stop", "close", "exit", "q"):
                try:
                    await page_msg.edit(
                        content=(
                            self._render_page(
                                pages[current_page],
                                current_page + 1,
                                total_pages,
                            )
                            + "\n\n*Help closed.*"
                        )
                    )
                except (discord.Forbidden, discord.NotFound):
                    pass
                break

            # Next page
            if text in ("next", "n", ">"):
                current_page = (current_page + 1) % total_pages

            # Previous page
            elif text in ("prev", "p", "<", "back", "b"):
                current_page = (current_page - 1) % total_pages

            # Jump to page number
            elif text.isdigit():
                target = int(text)
                if 1 <= target <= total_pages:
                    current_page = target - 1
                else:
                    continue  # ignore invalid page numbers silently

            else:
                continue  # ignore unrecognized messages

            # Update the page
            try:
                await page_msg.edit(
                    content=self._render_page(
                        pages[current_page], current_page + 1, total_pages
                    )
                )
            except (discord.Forbidden, discord.NotFound):
                break


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
