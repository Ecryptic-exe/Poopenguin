# pyright: reportAttributeAccessIssue=false
import asyncio
import contextlib
from collections.abc import Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    NotRequired,
    TypedDict,
    TypeVar,
    override,
)

import discord

from chuni_penguin.config import config
from chuni_penguin.logging import logger
from chuni_penguin.utils.chunithm_i18n import ui_text

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

ContextT = TypeVar("ContextT", bound="PenguinContext", covariant=True)


class MessageKwargs(TypedDict):
    content: NotRequired[str | None]
    embed: NotRequired[discord.Embed | None]
    embeds: NotRequired[Sequence[discord.Embed]]
    files: NotRequired[Sequence[discord.File]]
    suppress_embeds: NotRequired[bool]
    delete_after: NotRequired[float | None]
    allowed_mentions: NotRequired[discord.AllowedMentions | None]
    view: NotRequired[discord.ui.View | discord.ui.LayoutView]


class PenguinViewMixin(Generic[ContextT]):
    if TYPE_CHECKING:
        ctx: ContextT
        message: discord.Message | None

    async def _before_start(self, *, content: str | None = None) -> MessageKwargs:
        return {"content": content}

    async def start(
        self, *, content: str | None = None, ephemeral: bool = False
    ) -> discord.Message:
        kwargs = await self._before_start(content=content)

        from .panel import attach_navigation

        attach_navigation(self.ctx, self)

        from .dismiss import ensure_close

        ensure_close(self, self.ctx.author.id, ctx=self.ctx)

        # This will either be a discord.ui.View or discord.ui.LayoutView
        # depending on what you inherited it from.
        try:
            self.message = await self.ctx.respond_or_edit(  # pyright: ignore[reportCallIssue]
                **kwargs,
                view=self,  # pyright: ignore[reportArgumentType]
                ephemeral=ephemeral,
            )
        except asyncio.CancelledError:
            if hasattr(self, "deactivate"):
                await self.deactivate()
            if hasattr(self, "close_network"):
                await self.close_network()
            self.stop()
            raise
        from .panel import bind_panel_message

        bind_panel_message(self.ctx, self.message)
        return self.message  # pyright: ignore[reportReturnType]

    async def start_from(self, message: discord.Message, *, content: str | None = None):
        kwargs = await self._before_start(content=content)
        from .dismiss import ensure_close

        ensure_close(self, self.ctx.author.id, ctx=self.ctx)

        # Change some key names when unpacking kwargs into Message.edit
        if "suppress_embeds" in kwargs:
            kwargs["suppress"] = kwargs["suppress_embeds"]  # pyright: ignore[reportGeneralTypeIssues]
            del kwargs["suppress_embeds"]

        if "files" in kwargs:
            kwargs["attachments"] = kwargs["files"]  # pyright: ignore[reportGeneralTypeIssues]
            del kwargs["files"]

        # self will either be a discord.ui.View or discord.ui.LayoutView
        # depending on what you inherited it from.
        kwargs["view"] = self  # pyright: ignore[reportGeneralTypeIssues]
        kwargs["allowed_mentions"] = discord.AllowedMentions.none()

        self.message = message

        # suppress_embeds kwarg has already been fixed above.
        await message.edit(**kwargs)  # pyright: ignore[reportCallIssue]

    async def start_in(
        self, messageable: discord.abc.Messageable, *, content: str | None = None
    ) -> discord.Message:
        kwargs = await self._before_start(content=content)

        from .dismiss import ensure_close

        ensure_close(self, self.ctx.author.id, ctx=self.ctx)

        # self will either be a discord.ui.View or discord.ui.LayoutView
        # depending on what you inherited it from.
        kwargs["view"] = self  # pyright: ignore[reportGeneralTypeIssues]

        # Same reason as above.
        self.message = await messageable.send(**kwargs)  # pyright: ignore[reportCallIssue, reportArgumentType]
        return self.message  # pyright: ignore[reportReturnType]

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id == self.ctx.author.id:
            from .navigation import navigation_check

            return await navigation_check(self.ctx, interaction, self)

        await interaction.response.send_message(
            ui_text(self.ctx, "This menu cannot be controlled by you, sorry!"),
            ephemeral=True,
        )
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
        /,
    ) -> None:
        if isinstance(error, discord.NotFound):
            return

        from .dismiss import CloseView

        services = interaction.client.services
        errors = services.errors if services.available("errors") else None

        if errors is not None:
            embed, _ = await errors.construct_error_embed(interaction, None, error)

            if embed.description is not None:
                with contextlib.suppress(discord.NotFound):
                    if interaction.response.is_done():
                        await interaction.followup.send(
                            embed=embed,
                            ephemeral=True,
                            view=CloseView(self.ctx.author.id, ctx=self.ctx),
                        )
                    else:
                        await interaction.response.send_message(
                            embed=embed,
                            ephemeral=True,
                            view=CloseView(self.ctx.author.id, ctx=self.ctx),
                        )

                return

        await logger.aexception(
            "Unhandled view error", tag="view_error", exc_info=error
        )

        embed = discord.Embed(
            color=discord.Color.red(),
            title=ui_text(self.ctx, "操作未完成"),
            description=ui_text(self.ctx, "此操作暫時無法完成，請稍後重試。"),
        )

        if config.bot.support_server_invite:
            assert embed.description is not None

            embed.description += "\n"
            embed.description += (
                f"If this error keeps happening, please join the [support server]({config.bot.support_server_invite}) "
                "and report the bug in the #help-bugs channel!"
            )

        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
                view=CloseView(self.ctx.author.id, ctx=self.ctx),
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
                view=CloseView(self.ctx.author.id, ctx=self.ctx),
            )

        if errors is not None:
            await errors.submit_error_to_webhook(interaction, error)

    async def edit_message(self, interaction: discord.Interaction, **kwargs: Any):
        if getattr(self, "_panel_inactive", False):
            return
        from .dismiss import ensure_close

        ensure_close(self, self.ctx.author.id, ctx=self.ctx)
        from .panel import bind_panel_message, private_message

        if private_message(getattr(self, "message", None)):
            if interaction.response.is_done():
                self.message = await interaction.edit_original_response(
                    **kwargs, view=self
                )
            else:
                await interaction.response.edit_message(**kwargs, view=self)
                self.message = await interaction.original_response()
            bind_panel_message(self.ctx, self.message)
            return
        if interaction.response.is_done() and self.message is not None:
            if "suppress_embeds" in kwargs:
                kwargs["suppress"] = kwargs["suppress_embeds"]
                del kwargs["suppress_embeds"]

            await self.message.edit(
                **kwargs,
                view=self,  # pyright: ignore[reportArgumentType]
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if not interaction.response.is_done():
            await interaction.response.edit_message(**kwargs, view=self)  # pyright: ignore[reportArgumentType]
            return

        msg = "Interaction was responded to, and view does not have a message set."
        raise RuntimeError(msg)


class PenguinView(PenguinViewMixin[ContextT], discord.ui.View):
    """Common code for all views."""

    def __init__(self, ctx: ContextT, *, timeout: float | None = 180.0) -> None:
        discord.ui.View.__init__(self, timeout=timeout)

        self.ctx = ctx
        self.message = None

    @override
    async def on_timeout(self) -> None:
        from .navigation import expire_navigation

        await expire_navigation(self)


class PenguinLayoutView(PenguinViewMixin[ContextT], discord.ui.LayoutView):
    """Common code for all layout views."""

    def __init__(self, ctx: ContextT, *, timeout: float | None = 180.0) -> None:
        discord.ui.LayoutView.__init__(self, timeout=timeout)

        self.ctx = ctx

    @override
    async def on_timeout(self) -> None:
        # Remove top-level buttons and selects entirely
        for item in self.children:
            if isinstance(item, discord.ui.ActionRow):
                self.remove_item(item)

        # I'd like to remove all the interactables inside the layout view too,
        # but that's really complex, since you have to e.g. unwrap TextDisplays
        # from a Section. That's a problem for another time. For now, just disable
        # them.
        for item in self.walk_children():
            if (
                isinstance(item, discord.ui.Button)
                and item.style != discord.ButtonStyle.link
            ) or isinstance(item, discord.ui.Select):
                item.disabled = True

        if self.message is not None:
            from .dismiss import ensure_close

            replacement = discord.ui.LayoutView(timeout=None)
            for item in tuple(self.children):
                self.remove_item(item)
                replacement.add_item(item)
            ensure_close(replacement, self.ctx.author.id)

            await self.message.edit(
                view=replacement,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        self.stop()
