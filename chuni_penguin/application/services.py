"""Own shared resources for a Bot lifetime, independently of extension lifetimes."""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from chuni_penguin.application.errors import ServiceUnavailable
from chuni_penguin.config import Config
from chuni_penguin.identities import MappingError
from chuni_penguin.providers.chunilib_public import ChunilibPublicProvider
from chuni_penguin.providers.chunilib_snapshot import ChunilibSnapshotProvider
from chuni_penguin.providers.local_catalog import LocalCatalogProvider
from chuni_penguin.providers.registry import ProviderRegistry
from chuni_penguin.repositories.copypasta import CopypastaRepository
from chuni_penguin.repositories.courses import CourseRepository
from chuni_penguin.repositories.mapped_catalog import MappedCatalogRepository
from chuni_penguin.repositories.rating_snapshots import RatingSnapshotRepository
from chuni_penguin.repositories.runtime_info import RuntimeInfoRepository
from chuni_penguin.services.accounts import AccountService
from chuni_penguin.services.autocomplete import SongAutocompleteService
from chuni_penguin.services.catalog import UserSettingsService, UtilityServices
from chuni_penguin.services.catalog_query import CatalogQueryService
from chuni_penguin.services.chart_knowledge import ChartKnowledgeService
from chuni_penguin.services.command_errors import CommandErrorService
from chuni_penguin.services.copypasta_sessions import CopypastaService
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.services.game_sessions import GameSessionService
from chuni_penguin.services.mapped_catalog import MappedCatalogService
from chuni_penguin.services.permissions import PermissionService
from chuni_penguin.services.radio_sessions import RadioSessionService
from chuni_penguin.services.rating_analysis import RatingAnalysisService
from chuni_penguin.services.rating_runtime import RatingRuntime, RatingUnavailable
from chuni_penguin.services.rating_search import RatingSearchService
from chuni_penguin.services.web_application import WebApplicationService

if TYPE_CHECKING:
    import discord

    from chuni_penguin.bot import ChuniBot


def _rating_path(value: Path | None) -> Path:
    if value is None:
        msg = "Configure the explicit rating engine, associations, statistics and cache paths."
        raise RatingUnavailable(msg)
    return value


class ApplicationServices:
    def __init__(
        self,
        settings: Config,
        prefixes: dict[int, str],
        is_owner: Callable[..., Awaitable[bool]],
        current_user: Callable[[], "discord.ClientUser | None"],
        *,
        offline: bool,
        cache_dir: Path | None = None,
        bot_client: "ChuniBot | None" = None,
    ):
        self.settings = settings
        self.prefixes = prefixes
        self.is_owner = is_owner
        self.current_user = current_user
        self.offline = offline
        self.cache_dir = cache_dir
        self.bot_client = bot_client
        self._database: DatabaseService | None = None
        self._copypasta: CopypastaService | None = None
        self._courses: CourseRepository | None = None
        self._runtime_info: RuntimeInfoRepository | None = None
        self._users: UserSettingsService | None = None
        self._utils: UtilityServices | None = None
        self._autocomplete: SongAutocompleteService | None = None
        self._accounts: AccountService | None = None
        self._mapped_catalog: MappedCatalogService | None = None
        self._rating_search: RatingSearchService | None = None
        self._rating_analysis: RatingAnalysisService | None = None
        self._chart_knowledge: ChartKnowledgeService | None = None
        self._games: GameSessionService | None = None
        self._radios: RadioSessionService | None = None
        self._errors: CommandErrorService | None = None
        self._web: WebApplicationService | None = None
        self.permissions = PermissionService(None, is_owner)
        self.providers = ProviderRegistry()
        self.statuses: dict[str, dict[str, str]] = {}
        self._started = False
        self._closed = False

    def available(self, key: str) -> bool:
        return self.statuses.get(key, {}).get("state") == "ready"

    def require(self, key: str):
        if not self.available(key):
            raise ServiceUnavailable(key)

    def _ready(self, key: str):
        self.statuses[key] = {"state": "ready", "reason": ""}

    def _failed(self, key: str, exc: Exception):
        # Exception text may include credentials or connection strings.
        self.statuses[key] = {"state": "failed", "reason": type(exc).__name__}

    @property
    def database(self) -> DatabaseService:
        self.require("database")
        assert self._database is not None
        return self._database

    @property
    def copypasta(self) -> CopypastaService:
        self.require("copypasta")
        assert self._copypasta is not None
        return self._copypasta

    @property
    def courses(self) -> CourseRepository:
        self.require("database")
        if self._courses is None:
            self._courses = CourseRepository(self.database)
        return self._courses

    @property
    def runtime_info(self) -> RuntimeInfoRepository:
        self.require("database")
        if self._runtime_info is None:
            self._runtime_info = RuntimeInfoRepository(self.database)
        return self._runtime_info

    @property
    def users(self) -> UserSettingsService:
        self.require("users")
        assert self._users is not None
        return self._users

    @property
    def utils(self) -> UtilityServices:
        self.require("catalog")
        assert self._utils is not None
        return self._utils

    @property
    def autocomplete(self) -> SongAutocompleteService:
        self.require("catalog")
        assert self._autocomplete is not None
        return self._autocomplete

    @property
    def accounts(self) -> AccountService:
        self.require("accounts")
        assert self._accounts is not None
        return self._accounts

    async def start(self, required: set[str]):
        if self._closed:
            msg = "Application services are closed."
            raise RuntimeError(msg)
        if self._started:
            return
        known = {
            "database",
            "users",
            "permissions",
            "catalog",
            "accounts",
            "providers",
            "rating_search",
            "rating_analysis",
            "chart_knowledge",
            "copypasta",
            "games",
            "radios",
            "errors",
            "web",
        }
        if required - known:
            msg = "Unknown shared service requirement."
            raise ValueError(msg)
        self._started = True
        legacy_required = required - {
            "providers",
            "rating_search",
            "rating_analysis",
            "chart_knowledge",
            "copypasta",
            "games",
            "radios",
            "errors",
            "web",
        }
        if "a_catalog" in (
            key.strip() for key in self.settings.providers.enabled.split(",")
        ):
            legacy_required = legacy_required | {"database"}
        await self._start_legacy(legacy_required)
        if "errors" in required:
            if self.bot_client is None:
                self._failed("errors", ServiceUnavailable("errors"))
            else:
                try:
                    self._errors = CommandErrorService(self.bot_client)
                except Exception as exc:  # noqa: BLE001 - isolate optional client DI
                    self._failed("errors", exc)
                else:
                    self._ready("errors")
        if "web" in required:
            if self.bot_client is None:
                self._failed("web", ServiceUnavailable("web"))
            else:
                self._web = WebApplicationService(self.bot_client)
                self._ready("web")
        if "games" in required:
            self._games = GameSessionService()
            self._ready("games")
        if "radios" in required:
            self._radios = RadioSessionService()
            self._ready("radios")
        if "copypasta" in required:
            try:
                path = self.settings.copypasta.file
                if path is None:
                    msg = "Configure an existing private copypasta working-copy file."
                    raise ValueError(msg)
                self._copypasta = CopypastaService(CopypastaRepository(path))
            except Exception as exc:  # noqa: BLE001 - isolate optional local input
                self._failed("copypasta", exc)
            else:
                self._ready("copypasta")
        await self._start_providers(required="providers" in required)
        await self._start_rating(required)
        if "chart_knowledge" in required:
            try:
                self._chart_knowledge = await asyncio.to_thread(
                    ChartKnowledgeService.load,
                    self.settings.knowledge.manual_file,
                    self.settings.knowledge.bindings_file,
                    self.settings.knowledge.semantic_root,
                    self.settings.knowledge.semantic_catalogue_id,
                )
            except Exception as exc:  # noqa: BLE001 - isolate optional annotation input
                self._failed("chart_knowledge", exc)
            else:
                self._ready("chart_knowledge")
        if self.available("database"):
            try:
                self._mapped_catalog = MappedCatalogService(
                    self.providers,
                    MappedCatalogRepository(self.database.mapping_read_sessionmaker),
                )
            except MappingError:
                # Unsupported optional read-only URL cannot block legacy Cogs.
                self._mapped_catalog = None

    @property
    def errors(self) -> CommandErrorService:
        self.require("errors")
        assert self._errors is not None
        return self._errors

    @property
    def web(self) -> WebApplicationService:
        self.require("web")
        assert self._web is not None
        return self._web

    @property
    def radios(self) -> RadioSessionService:
        self.require("radios")
        assert self._radios is not None
        return self._radios

    @property
    def games(self) -> GameSessionService:
        self.require("games")
        assert self._games is not None
        return self._games

    @property
    def chart_knowledge(self) -> ChartKnowledgeService:
        self.require("chart_knowledge")
        assert self._chart_knowledge is not None
        return self._chart_knowledge

    @property
    def rating_search(self) -> RatingSearchService:
        self.require("rating_search")
        assert self._rating_search is not None
        return self._rating_search

    @property
    def rating_analysis(self) -> RatingAnalysisService:
        self.require("rating_analysis")
        assert self._rating_analysis is not None
        return self._rating_analysis

    async def _start_rating(self, required: set[str]):
        if not required.intersection({"rating_search", "rating_analysis"}):
            return
        settings = self.settings.rating
        try:
            self.require("providers")
            runtime = await asyncio.to_thread(
                RatingRuntime, _rating_path(settings.engine_root)
            )
            self._rating_search = await RatingSearchService.start(
                runtime, self.providers, settings.catalog_provider
            )
        except Exception as exc:  # noqa: BLE001 - isolate optional C integration
            self._failed("rating_search", exc)
        else:
            self._ready("rating_search")
        if "rating_analysis" not in required:
            return
        if not self.available("rating_search"):
            self.statuses["rating_analysis"] = {
                "state": "blocked",
                "reason": "rating_search unavailable",
            }
            return
        try:
            repository = RatingSnapshotRepository(
                _rating_path(settings.associations),
                _rating_path(settings.statistics_root),
                self.rating_search.snapshot,
            )
            options = {
                name: getattr(settings, name)
                for name in (
                    "min_players",
                    "max_workers",
                    "queue_limit",
                    "cache_ttl",
                    "worker_timeout",
                )
            }
            self._rating_analysis = RatingAnalysisService(
                self.rating_search,
                repository,
                _rating_path(self.cache_dir) / "rating",
                options=options,
            )
        except Exception as exc:  # noqa: BLE001
            self._failed("rating_analysis", exc)
        else:
            self._ready("rating_analysis")

    @property
    def catalog_query(self) -> CatalogQueryService:
        self.require("providers")
        return CatalogQueryService(self.providers)

    @property
    def mapped_catalog(self) -> MappedCatalogService:
        self.require("database")
        if self._mapped_catalog is None:
            key = "mapped_catalog"
            raise ServiceUnavailable(key)
        return self._mapped_catalog

    async def _start_providers(self, *, required: bool):
        enabled = tuple(
            dict.fromkeys(
                key.strip()
                for key in self.settings.providers.enabled.split(",")
                if key.strip()
            )
        )
        if not enabled:
            if required:
                self.statuses["providers"] = {
                    "state": "disabled",
                    "reason": "not configured",
                }
            await self.providers.start()
            return
        try:
            self.providers = ProviderRegistry(
                timeout=self.settings.providers.timeout_seconds
            )
        except ValueError as exc:
            self.statuses["providers"] = {
                "state": "degraded",
                "reason": type(exc).__name__,
            }
            return
        configuration_error = False
        for key in enabled:
            try:
                if key == "a_catalog":
                    self.providers.register(
                        LocalCatalogProvider(
                            self._database if self.available("database") else None
                        )
                    )
                elif key == "chunilib_snapshot":
                    path = self.settings.providers.chunilib_snapshot_path
                    if path is None:
                        configuration_error = True
                        continue
                    self.providers.register(
                        ChunilibSnapshotProvider(
                            path, self.settings.providers.chunilib_game_version
                        )
                    )
                elif key == "chunilib_public":
                    if self.offline:
                        configuration_error = True
                        continue
                    self.providers.register(
                        ChunilibPublicProvider(
                            self.settings.providers.chunilib_game_version
                        )
                    )
                else:
                    configuration_error = True
            except Exception:  # noqa: BLE001 - one invalid source must not block others
                configuration_error = True
        await self.providers.start()
        states = self.providers.status().values()
        if any(s["ready"] for s in states):
            partial = configuration_error or any(not s["ready"] for s in states)
            self.statuses["providers"] = {
                "state": "ready",
                "reason": "partial" if partial else "",
            }
        else:
            self.statuses["providers"] = {
                # The registry exists, but has no usable source. Consumers that
                # require it are blocked; unrelated command trees stay complete.
                "state": "degraded",
                "reason": "no ready sources",
            }

    async def _start_legacy(self, required: set[str]):
        known = {"database", "users", "permissions", "catalog", "accounts"}
        if not required:
            return
        active = required | {"database", "users", "permissions"}
        for key in known - active:
            self.statuses[key] = {"state": "disabled", "reason": "not required"}
        try:
            self._database = DatabaseService(self.settings)
            await self._database.start()
        except Exception as exc:  # noqa: BLE001 - isolate shared-resource startup
            self._failed("database", exc)
            if self._database is not None:
                with contextlib.suppress(Exception):
                    await self._database.close()
            for key in ("users", "catalog", "permissions", "accounts"):
                if key in active:
                    self.statuses[key] = {"state": "blocked", "reason": "database"}
            return

        self._ready("database")
        self._users = UserSettingsService(self.database, self.settings, self.prefixes)
        try:
            await self._users.fetch_user_config(0)
        except Exception as exc:  # noqa: BLE001
            self._failed("users", exc)
        else:
            self._ready("users")
        self.permissions = PermissionService(self.database, self.is_owner)
        try:
            await self.permissions.start()
        except Exception as exc:  # noqa: BLE001
            self._failed("permissions", exc)
        else:
            self._ready("permissions")

        if "catalog" in active:
            self._utils = UtilityServices(self.database, self._users)
            self._autocomplete = SongAutocompleteService(self._utils.aliases)
            try:
                await self._utils.aliases.reload()
            except Exception as exc:  # noqa: BLE001
                self._failed("catalog", exc)
            else:
                self._ready("catalog")

        if "accounts" not in active:
            return
        if not self.available("users"):
            self.statuses["accounts"] = {"state": "blocked", "reason": "users"}
            return
        self._accounts = AccountService(
            self.database,
            self.users,
            self.settings,
            self.current_user,
            offline=self.offline,
        )
        try:
            await self._accounts.start()
        except Exception as exc:  # noqa: BLE001
            self._failed("accounts", exc)
        else:
            self._ready("accounts")

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._copypasta is not None:
                self._copypasta.close()
        finally:
            try:
                if self._rating_analysis is not None:
                    await self._rating_analysis.close()
            finally:
                try:
                    try:
                        if self._mapped_catalog is not None:
                            await self._mapped_catalog.close()
                    finally:
                        await self.providers.close()
                finally:
                    await self._close_legacy()

    async def _close_legacy(self):
        try:
            try:
                try:
                    try:
                        if self._web is not None:
                            await self._web.close()
                    finally:
                        if self._radios is not None:
                            await self._radios.close()
                finally:
                    if self._games is not None:
                        await self._games.close(self.current_user())
            finally:
                if self._accounts is not None:
                    await self._accounts.close()
        finally:
            try:
                if self._database is not None:
                    await self._database.close()
            finally:
                self.permissions.ready = False
                for key, status in self.statuses.items():
                    if status["state"] == "ready":
                        self.statuses[key] = {"state": "closed", "reason": ""}
