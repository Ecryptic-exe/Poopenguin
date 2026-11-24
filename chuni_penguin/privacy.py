"""Private delivery policy shared by text and interaction entry points."""

import asyncio
from datetime import UTC, datetime

from discord.ext import commands
from sqlalchemy.exc import SQLAlchemyError

from chuni_penguin.logging import logger

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


async def private_score_response(interaction) -> bool:
    """Choose visibility before Discord locks the initial response to it."""
    age = (datetime.now(UTC) - interaction.created_at).total_seconds()
    budget = min(0.75, 2.4 - age)
    if budget <= 0:
        return True
    try:
        async with asyncio.timeout(budget):
            settings = await interaction.client.services.users.fetch_user_config(
                interaction.user.id
            )
    except (TimeoutError, OSError, SQLAlchemyError) as error:
        await logger.awarning(
            "Score visibility lookup failed", error_type=type(error).__name__
        )
        return True
    return settings.privacy_mode
