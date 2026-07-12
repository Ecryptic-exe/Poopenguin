"""
Small standalone "for fun" commands that don't belong anywhere else:
!ask, !pick, !rng, !rcg / /ask, /pick, /rng, /rcg.

These were plain bot.command() functions at the bottom of the old
vto.py. Behaviour is unchanged - only the language branching was
routed through i18n.t() to cut down on repetition.

Converted to commands.hybrid_command so each one is usable both as a
"!" text command and a "/" slash command from a single implementation.

`pick` used to take `*choices` (any number of positional args), which
discord's slash commands don't support (slash options are a fixed
list, not open-ended). It's now a single `choices` string that's
split the same way the old *args parsing did (shlex-aware, so
`!pick "New York" "Los Angeles"` still treats each quoted phrase as
one choice, for both ! and /).
"""
import random
import shlex

import discord
from discord.ext import commands

from config import load_settings
from i18n import t, get_guild_language


class GeneralCog(commands.Cog, name="general"):
    def __init__(self, bot):
        self.bot = bot

    def _lang(self, ctx):
        return get_guild_language(load_settings(), ctx.guild.id)

    @commands.hybrid_command(
        name="ask",
        description="Asks a question and receives a response based on a random success rate.")
    @discord.app_commands.describe(question="The question you want answered")
    async def ask(self, ctx, *, question: str):
        """Asks a question and receives a response based on a random success rate."""
        language = self._lang(ctx)
        success_rate = random.random() * 100

        if success_rate >= 80:
            response = t(language,
                f"Regarding '{question}', it looks very likely to succeed!",
                f"關於 '{question}'，看起來非常有可能成功！")
        elif success_rate >= 50:
            response = t(language,
                f"For '{question}', there's a decent chance it could happen.",
                f"對於 '{question}'，有不錯的機會可能會發生。")
        elif success_rate >= 20:
            response = t(language,
                f"About '{question}', it's not very likely, but who knows?",
                f"關於 '{question}'，不太可能，但誰知道呢？")
        else:
            response = t(language,
                f"Sorry, for '{question}', it seems quite unlikely.",
                f"抱歉，對於 '{question}'，看起來相當不可能。")

        await ctx.send(response)

    @commands.hybrid_command(
        name="pick",
        description="Randomly selects one option from a list of provided choices.")
    @discord.app_commands.describe(
        choices="Choices to pick from, separated by spaces "
                '(wrap multi-word choices in quotes, e.g. "New York" "Los Angeles")')
    async def pick(self, ctx, *, choices: str = None):
        """Randomly selects one option from a list of provided choices."""
        language = self._lang(ctx)
        if not choices:
            await ctx.send(t(language, "Please provide at least one choice.", "請提供至少一個選項。"))
            return

        try:
            choice_list = shlex.split(choices)
        except ValueError:
            # Unbalanced quotes - fall back to a plain whitespace split
            # rather than erroring out on the user.
            choice_list = choices.split()

        if not choice_list:
            await ctx.send(t(language, "Please provide at least one choice.", "請提供至少一個選項。"))
            return

        choice = random.choice(choice_list)
        await ctx.send(t(language, f"I picked: {choice}", f"我選擇了：{choice}"))

    @commands.hybrid_command(
        name="rng",
        description="Generates a random number between a specified minimum and maximum.")
    @discord.app_commands.describe(
        min_val="Minimum value (default 1)",
        max_val="Maximum value (default 100)",
        type="'int' or 'float' (default int)")
    async def rng(self, ctx, min_val: str = "1", max_val: str = "100", type: str = "int"):
        """Generates a random number between a specified minimum and maximum."""
        language = self._lang(ctx)

        if type.lower() not in ("int", "float"):
            await ctx.send(t(language, "Type must be 'int' or 'float'.", "類型必須是 'int' 或 'float'。"))
            return

        try:
            min_num = float(min_val)
            max_num = float(max_val)
        except ValueError:
            await ctx.send(t(language, "Min and max must be valid numbers.", "最小值和最大值必須是有效數字。"))
            return

        if min_num > max_num:
            await ctx.send(t(language,
                "Minimum value must be less than or equal to maximum value.",
                "最小值必須小於或等於最大值。"))
            return

        if type.lower() == "int":
            result = random.randint(int(min_num), int(max_num))
        else:
            result = round(random.uniform(min_num, max_num), 2)

        await ctx.send(t(language, f"Random number: {result}", f"隨機數：{result}"))

    @commands.hybrid_command(
        name="rcg",
        description="Generates a random color in hexadecimal format with a preview.")
    async def rcg(self, ctx):
        """Generates a random color in hexadecimal format with a preview."""
        language = self._lang(ctx)
        r, g, b = (random.randint(0, 255) for _ in range(3))
        color_hex = f"#{r:02x}{g:02x}{b:02x}".upper()

        embed = discord.Embed(
            title=t(language, "Random Color", "隨機顏色"),
            description=f"Hex: {color_hex}",
            color=discord.Color.from_rgb(r, g, b)
        )
        embed.add_field(name=t(language, "RGB", "RGB值"), value=f"({r}, {g}, {b})", inline=True)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
