import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast, override

import discord
import hishel
import httpx
import httpx_aiohttp
from discord.ext import commands
from sqlalchemy import select, text

from .application.extensions import ExtensionManager, ExtensionSpec
from .application.lifecycle import ManagedEditTrackerCog
from .application.services import ApplicationServices
from .cogs import DEFAULT_EXTENSIONS
from .command_tree import PenguinCommandTree
from .config import Config, get_config, use_config
from .constants import CACHE_DIR
from .context import PenguinContext, PenguinGuildContext
from .database import Prefix, UserConfig
from .logging import logger
from .privacy import private_text_check
from .utils import HishelMsgspecSerializer

if TYPE_CHECKING:
    from .services.accounts import AccountService
    from .services.catalog import UtilityServices
    from .services.database import DatabaseService
    from .services.permissions import PermissionService


def guild_specific_prefix(default: str):
    async def inner(bot: ChuniBot, msg: discord.Message) -> list[str]:
        prefixes = commands.when_mentioned(bot, msg)

        if msg.guild is None:
            prefixes.append(default)
        else:
            prefixes.append(bot.prefixes.get(msg.guild.id, default))

            if (role := msg.guild.self_role) is not None:
                prefixes.append(f"{role.mention} ")

        return prefixes

    return inner


def ensure_text_command_permissions():
    check = commands.bot_has_permissions(
        send_messages=True,
        embed_links=True,
    )
    thread_check = commands.bot_has_permissions(
        send_messages_in_threads=True,
        embed_links=True,
    )

    async def predicate(ctx: commands.Context):
        if ctx.interaction is not None:
            return True

        if isinstance(ctx.channel, discord.Thread):
            return await thread_check.predicate(ctx)

        return await check.predicate(ctx)

    return commands.check(predicate)


class ChuniBot(commands.AutoShardedBot):
    if TYPE_CHECKING:
        _BotBase__extensions: dict[str, ModuleType]
        _BotBase__cogs: dict[str, commands.Cog]

    def __init__(
        self,
        settings: Config | None = None,
        *,
        offline: bool = False,
        extensions: tuple[ExtensionSpec, ...] = DEFAULT_EXTENSIONS,
        cache_dir: Path = CACHE_DIR,
    ):
        self.settings = settings or get_config()
        self.offline = offline
        # Intents explanation:
        # - Guilds: needed for basically anything and the library does not recommend
        # turning it off
        # - Voice states: Needed for c>guess voice and c>radio to handle bot moves, and
        # to detect what VC the user is in
        # - Messages/message content: Prefix command handling
        # - Typing: c>guess activity detection
        super().__init__(
            max_messages=20000,
            command_prefix=guild_specific_prefix(self.settings.bot.default_prefix),
            tree_cls=PenguinCommandTree,
            intents=discord.Intents(
                guilds=True,
                voice_states=True,
                messages=True,
                typing=True,
                message_content=True,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self.add_check(ensure_text_command_permissions().predicate)

        self.launch_time: float = -1
        self.prefixes: dict[int, str] = {}
        self.services = ApplicationServices(
            self.settings,
            self.prefixes,
            self.is_owner,
            lambda: self.user,
            offline=self.offline,
            cache_dir=cache_dir,
            bot_client=self,
        )
        self.add_check(self._check_permissions)
        self.add_check(private_text_check)
        self.command_start_time: dict[commands.Context, int] = {}
        self.caching_http_client = hishel.AsyncCacheClient(
            timeout=httpx.Timeout(timeout=60.0),
            follow_redirects=True,
            transport=httpx_aiohttp.AIOHTTPTransport(retries=5),
            controller=hishel.Controller(
                cacheable_methods=["GET", "HEAD"],
                allow_heuristics=True,
                allow_stale=True,
            ),
            storage=hishel.AsyncFileStorage(
                serializer=HishelMsgspecSerializer(),
                base_path=cache_dir,
                check_ttl_every=300,
            ),
        )

        self.extension_manager = ExtensionManager(self, extensions)
        self.startup_report = self.extension_manager.report

    async def start(self, *args, **kwargs):
        if self.offline:
            msg = "Offline bots cannot connect to the Discord gateway."
            raise RuntimeError(msg)
        self.launch_time = time.time()
        with use_config(self.settings):
            return await super().start(*args, **kwargs)

    async def load_extension(self, name: str, *, package: str | None = None) -> None:
        with use_config(self.settings):
            await super().load_extension(name, package=package)
            spec = self.extension_manager.specs.get(name)
            if spec is not None and any(
                self.get_cog(n) is None for n in spec.cog_names
            ):
                await super().unload_extension(name, package=package)
                # Raising inside load_extension lets native reload restore the old
                # module as well as its commands when setup returns without a Cog.
                error = RuntimeError("Expected Cog missing after extension setup.")
                raise commands.ExtensionFailed(name, error)

    async def setup_hook(self) -> None:
        with use_config(self.settings):
            await self._setup_extensions()

    async def _setup_extensions(self) -> None:
        selected = self.extension_manager.selected_specs(
            enabled=self.settings.cogs.enabled,
            disabled=self.settings.cogs.disabled,
            extra=self.settings.cogs.extra,
        )
        required_services = {key for spec in selected for key in spec.required_services}
        await self.services.start(required_services)
        self.startup_report.services = {
            key: dict(value) for key, value in self.services.statuses.items()
        }
        # Database setup
        if not self.offline:
            with contextlib.suppress(
                commands.ExtensionNotFound, commands.ExtensionFailed
            ):
                await self.load_extension("chuni_penguin.cogs.debug")

        await self.add_cog(
            ManagedEditTrackerCog(self, max_duration=timedelta(minutes=5))
        )
        await self.extension_manager.load_all(
            enabled=self.settings.cogs.enabled,
            disabled=self.settings.cogs.disabled,
            extra=self.settings.cogs.extra,
        )
        from .ui.entrypoints import configure_entrypoints

        configure_entrypoints(self)
        await logger.ainfo(
            "Extension startup finished", startup=self.startup_report.as_dict()
        )
        if not self.services.available("database"):
            if required_services - {
                "providers",
                "rating_search",
                "rating_analysis",
                "chart_knowledge",
                "games",
                "radios",
                "errors",
                "web",
            }:
                self.startup_report.post_setup_error = "database unavailable"
            else:
                # Service-free and source-only selections need no database. Global
                # policy still denies non-owners until its store is initialized.
                self.startup_report.registration = "pending_manual_sync"
            return

        try:
            await self._load_startup_data()
        except Exception as exc:  # noqa: BLE001
            self.startup_report.post_setup_error = type(exc).__name__
            await logger.aerror(
                "Startup data unavailable", error_type=type(exc).__name__
            )

    async def _load_startup_data(self):
        # Load guild prefixes
        async with self.begin_db_read() as session:
            prefixes = (await session.execute(select(Prefix))).scalars()

        self.prefixes.clear()
        self.prefixes.update({prefix.guild_id: prefix.prefix for prefix in prefixes})
        self.startup_report.database_ready = True

        await logger.ainfo(
            "Loaded guild prefixes",
            tag="load_guild_prefix",
            prefix_count=len(self.prefixes),
        )

        tree = cast(PenguinCommandTree, self.tree)
        current_tree_hash = await tree.get_hash()

        # very much an abuse but i can't be asked to add yet another database table
        # nor use a temp file since i have to parse string back to number
        async with self.begin_db_read() as session:
            result = await session.execute(text("PRAGMA user_version"))
            old_tree_hash: int | None = result.scalar_one_or_none()

        if old_tree_hash != current_tree_hash:
            await logger.ainfo(
                "Command tree updated",
                tag="command_tree_updated",
                old_hash=old_tree_hash,
                new_hash=current_tree_hash,
            )

            # A partial startup must never replace the remote tree. Explicit owner
            # treesync remains available; startup only reports the difference.
            self.startup_report.registration = "pending_manual_sync"

    @override
    async def get_context(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        origin: discord.Message | discord.Interaction,
        *,
        cls: type[PenguinContext] = PenguinContext,
    ):
        if origin.guild is not None:
            cls = PenguinGuildContext

        try:
            ctx = await super().get_context(origin, cls=cls)
        except Exception as e:
            await logger.aexception(
                "could not get context", tag="error_get_context", exc_info=e
            )
            raise

        if ctx.command is not None:
            ctx.user_config = await self.fetch_user_config(ctx.author.id)

        return ctx

    async def fetch_user_config(self, user_id: int) -> UserConfig:
        if self.services.available("users"):
            return await self.services.users.fetch_user_config(user_id)
        # Only presentation defaults. The global policy remains fail-closed for
        # non-owners until its service is ready, so this cannot grant access.
        return UserConfig(
            discord_id=user_id, synthesis_alt_jacket="default", privacy_mode=True
        )

    async def _check_permissions(self, ctx: commands.Context):
        return await self.services.permissions.check(cast(PenguinContext, ctx))

    async def record_global_command_sync(self):
        current_hash = await cast(PenguinCommandTree, self.tree).get_hash()
        try:
            await self.database.writer.execute(
                text(f"PRAGMA user_version={current_hash}"), transaction=False
            )
        except Exception as exc:  # noqa: BLE001
            self.startup_report.registration = "synced_local_hash_unrecorded"
            await logger.aerror(
                "Command registration succeeded; local hash not saved",
                error_type=type(exc).__name__,
            )
        else:
            self.startup_report.registration = "synchronized"

    @override
    async def _run_event(
        self,
        coro: Callable[..., Coroutine[Any, Any, Any]],
        event_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        try:
            await coro(*args, **kwargs)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            await logger.aerror(
                "exception in event handler",
                tag="error_event_handler",
                event_method=event_name,
                args=args,
                kwargs=kwargs,
                exc_info=e,
            )

            with contextlib.suppress(asyncio.CancelledError):
                await self.on_error(event_name, *args, **kwargs)

    @override
    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        pass

    @property
    def utils(self) -> "UtilityServices":
        return self.services.utils

    @property
    def database(self) -> "DatabaseService":
        return self.services.database

    @property
    def begin_db_read(self):
        return self.database.read_sessionmaker

    @property
    def app(self):
        return self.services.web.app if self.services.available("web") else None

    @property
    def chunithm_networks(self) -> "AccountService":
        return self.services.accounts

    @property
    def permissions(self) -> "PermissionService":
        return self.services.permissions

    async def _close_games(self):
        if self.services.available("games"):
            await self.services.games.shutdown(self.user)

    async def close(self) -> None:
        await self._close_games()

        timeout_tasks: set[asyncio.Task] = set()

        for view in self._connection._view_store._synced_message_views.values():
            if view.is_finished():
                continue

            if view.timeout is not None:
                timeout_tasks.add(
                    asyncio.create_task(
                        view.on_timeout(), name=f"discord-ui-view-timeout-{view.id}"
                    )
                )

        if len(timeout_tasks) > 0:
            await asyncio.wait(timeout_tasks)

        # Unload extensions in reverse order (LIFO) since later extensions depend
        # on previous extensions being available - discord.py's default behavior is FIFO
        for extension in reversed(tuple(self._BotBase__extensions)):
            with contextlib.suppress(Exception):
                await self.unload_extension(extension)

        for cog in reversed(tuple(self._BotBase__cogs)):
            with contextlib.suppress(Exception):
                await self.remove_cog(cog)

        try:
            await super().close()
        finally:
            try:
                await self.services.close()
            finally:
                await self.caching_http_client.aclose()
