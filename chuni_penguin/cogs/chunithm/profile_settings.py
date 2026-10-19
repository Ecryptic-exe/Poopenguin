"""Local user settings, independent of catalog and remote accounts."""

from typing import TYPE_CHECKING, Literal

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from chuni_penguin.database import UserConfig
from chuni_penguin.logging import logged_prefix_command

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ProfileSettingsCog(commands.Cog, name="ProfileSettings"):
    def __init__(self, bot: "ChuniBot"):
        self.users = bot.services.users

    @commands.hybrid_command("config")
    @app_commands.describe(
        key="The option you want to change or view.",
        value="The value to change the option to. Leave blank to see the current value.",
    )
    @app_commands.choices(
        key=[
            app_commands.Choice(
                name="synthesis-alt-jacket", value="synthesis-alt-jacket"
            ),
            app_commands.Choice(name="privacy", value="privacy"),
        ]
    )
    @logged_prefix_command
    async def config(
        self,
        ctx: Context,
        key: Literal["synthesis-alt-jacket", "privacy"],
        value: str | None = None,
    ):
        """Adjust your experience with the bot.

        Currently, these options are supported:
        - `synthesis-alt-jacket`: Changes the jacket art for the song "Synthesis." whereever applicable. The possible options are `none` (black background), `default` (use CHUNITHM's jacket art), `cytus2`, `vividstasis`, `musedash`, `musicdiver`, `sdvx`, `paradigmreboot`, `lanota` and `deemo2`.
        - `privacy`: Do not allow other users to view your profile and scores using the bot. You can still use commands, but to others it will seem like you're not logged in. The possible options are `true` (enabled) and `false` (disabled).

        **Parameters:**
        `key`: The option you want to change or view.
        `value`: The value to change the option to. Leave blank to see the current value for the given option.

        **Examples:**
        `/config synthesis-alt-jacket cytus2`
        `/config privacy true`
        """

        if key != "synthesis-alt-jacket" and key != "privacy":
            msg = "Expected option to be `synthesis-alt-jacket` or `privacy`."
            raise ValueError(msg)

        new_config = False

        user_config = await self.users.stored_user_config(ctx.author.id)

        if value is None:
            if key == "synthesis-alt-jacket":
                current_value = (
                    user_config.synthesis_alt_jacket if user_config else "default"
                )
            else:
                current_value = (
                    str(user_config.privacy_mode) if user_config else "False"
                )

            await ctx.reply(
                content=f"Your current config for `{key}` is `{current_value}`.",
                mention_author=False,
            )
            return

        value = value.lower()

        if user_config is None:
            new_config = True
            user_config = UserConfig(
                discord_id=ctx.author.id,
                synthesis_alt_jacket="default",
                privacy_mode=False,
            )

        if key == "synthesis-alt-jacket":
            if value not in (
                "none",
                "default",
                "cytus2",
                "vividstasis",
                "musedash",
                "musicdiver",
                "sdvx",
                "paradigmreboot",
                "lanota",
                "deemo2",
            ):
                msg = "Invalid option for `synthesis-alt-jacket`. Expected one of `none`, `default`, `cytus2`, `vividstasis`, `musedash`, `musicdiver`, `sdvx`, `paradigmreboot`, `lanota` or `deemo2`."
                raise commands.BadArgument(msg)

            user_config.synthesis_alt_jacket = value
        else:
            if value not in (
                "1",
                "true",
                "t",
                "yes",
                "y",
                "on",
                "0",
                "false",
                "f",
                "no",
                "n",
                "off",
            ):
                msg = "Invalid option for `privacy`. Expected one of `true` or `false`."
                raise commands.BadArgument(msg)

            user_config.privacy_mode = value in ("1", "true", "t", "yes", "y", "on")

        await self.users.save_user_config(user_config, is_new=new_config)

        await ctx.reply(
            content=f"Set your config for `{key}` to `{value}`.",
            mention_author=False,
        )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ProfileSettingsCog(bot))
