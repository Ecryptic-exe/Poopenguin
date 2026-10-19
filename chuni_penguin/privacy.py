"""Private delivery policy shared by text and interaction entry points."""

from discord.ext import commands

PRIVATE_COMMANDS = frozenset(
    {
        "sync",
        "refresh",
        "update",
        "login",
        "logout",
        "privacy",
        "export",
        "delete",
        "token",
    }
)


def private_command(command) -> bool:
    if command is None:
        return False
    return (
        command.qualified_name.split(" ")[0] in PRIVATE_COMMANDS
        or command.qualified_name in {"account delete", "account unlink", "account link"}
    )


async def private_text_check(ctx):
    if (
        ctx.guild is not None
        and ctx.interaction is None
        and private_command(ctx.command)
    ):
        raise commands.CheckFailure(
            "此操作包含個人資料，請使用斜線指令，或在 Bot 私訊中操作。"
        )
    return True
