import asyncio
import importlib
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ExtensionState(StrEnum):
    LOADED = "loaded"
    DISABLED = "disabled"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNLOADED = "unloaded"


@dataclass(frozen=True, slots=True)
class ExtensionSpec:
    module: str
    cog_names: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    optional: bool = False
    required_services: tuple[str, ...] = ()
    # Startup compatibility when splitting an existing configured domain.
    selection_parents: tuple[str, ...] = ()
    default_enabled: bool = True
    # Deprecated import/configuration names that resolve to this canonical spec.
    aliases: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.module.removeprefix("chuni_penguin.cogs.")


@dataclass(frozen=True, slots=True)
class ExtensionStatus:
    spec: ExtensionSpec
    state: ExtensionState
    reason: str = ""
    # No exception messages or tracebacks: imports may contain secret-bearing URLs.


class ExtensionStatusMap(dict[str, "ExtensionStatus"]):
    """Canonical statuses with lookup compatibility for renamed extensions."""

    def __init__(self):
        super().__init__()
        self._aliases: dict[str, str] = {}

    def configure_aliases(self, specs: tuple[ExtensionSpec, ...]) -> None:
        self._aliases = {alias: spec.module for spec in specs for alias in spec.aliases}

    @property
    def aliases(self) -> dict[str, str]:
        return self._aliases.copy()

    def __getitem__(self, key: str) -> "ExtensionStatus":
        return super().__getitem__(self._aliases.get(key, key))

    def get(self, key: str, default=None):
        return super().get(self._aliases.get(key, key), default)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            key = self._aliases.get(key, key)
        return super().__contains__(key)


@dataclass(slots=True)
class StartupReport:
    extensions: ExtensionStatusMap = field(default_factory=ExtensionStatusMap)
    services: dict[str, dict[str, str]] = field(default_factory=dict)
    database_ready: bool = False
    post_setup_error: str = ""
    registration: str = "manual"

    @property
    def complete(self) -> bool:
        return (
            not self.post_setup_error
            and all(
                status["state"] not in ("failed", "blocked")
                for status in self.services.values()
            )
            and all(
                s.state in (ExtensionState.LOADED, ExtensionState.DISABLED)
                or s.spec.optional
                for s in self.extensions.values()
            )
        )

    def as_dict(self) -> dict:
        extensions = {
            name: {"state": status.state.value, "reason": status.reason}
            for name, status in self.extensions.items()
        }
        return {
            "complete": self.complete,
            "database_ready": self.database_ready,
            "post_setup_error": self.post_setup_error,
            "registration": self.registration,
            "extensions": extensions,
            "services": self.services,
        }


class ExtensionManager:
    """Use discord.py's registration rollback; retain explicit legacy dependencies."""

    def __init__(self, bot: "ChuniBot", specs: tuple[ExtensionSpec, ...]):
        self.bot = bot
        self.specs = {spec.module: spec for spec in specs}
        if len(self.specs) != len(specs):
            msg = "Duplicate extension module in startup configuration."
            raise ValueError(msg)
        self.report = StartupReport()
        self.report.extensions.configure_aliases(specs)
        self._lock = asyncio.Lock()
        self._install_alias_lifecycle_hooks()

    def _install_alias_lifecycle_hooks(self) -> None:
        """Keep legacy module aliases in step with discord.py extension lifecycles."""
        for method_name in (
            "load_extension",
            "unload_extension",
            "reload_extension",
        ):
            original = getattr(self.bot, method_name, None)
            if not callable(original) or getattr(
                original, "_extension_alias_sync_hook", False
            ):
                continue

            async def hooked(*args, _original=original, **kwargs):
                try:
                    return await _original(*args, **kwargs)
                finally:
                    module_name = args[0] if args else kwargs.get("name")
                    if isinstance(module_name, str):
                        try:
                            spec = self.resolve(module_name)
                        except ValueError:
                            spec = None
                        if spec is not None and spec.aliases:
                            self._sync_alias_modules(spec)

            hooked._extension_alias_sync_hook = True
            setattr(self.bot, method_name, hooked)

    @staticmethod
    def _sync_alias_modules(spec: ExtensionSpec) -> None:
        """Refresh canonical/legacy package references across extension lifecycles."""
        if not spec.aliases:
            return
        canonical = sys.modules.get(spec.module)
        for module_name in (spec.module, *spec.aliases):
            parent_name, _, child_name = module_name.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            if canonical is not None and parent is None and parent_name:
                try:
                    parent = importlib.import_module(parent_name)
                except ModuleNotFoundError:
                    parent = None
            if canonical is None:
                if module_name != spec.module:
                    sys.modules.pop(module_name, None)
                if parent is not None:
                    reference = getattr(parent, child_name, None)
                    if getattr(reference, "__name__", None) == spec.module:
                        delattr(parent, child_name)
                continue
            if module_name != spec.module:
                sys.modules[module_name] = canonical
            if parent is not None:
                setattr(parent, child_name, canonical)

    def resolve(self, key: str) -> ExtensionSpec:
        matches = [
            s for s in self.specs.values() if key in (s.module, s.key, *s.aliases)
        ]
        if len(matches) != 1:
            msg = "Unknown or ambiguous extension key."
            raise ValueError(msg)
        return matches[0]

    def _loaded(self, spec: ExtensionSpec) -> bool:
        return (
            all(self.bot.services.available(key) for key in spec.required_services)
            and spec.module in self.bot.extensions
            and all(self.bot.get_cog(name) is not None for name in spec.cog_names)
        )

    def _status(
        self, spec: ExtensionSpec, state: ExtensionState, reason: str = ""
    ) -> ExtensionStatus:
        status = ExtensionStatus(spec, state, reason)
        self.report.extensions[spec.module] = status
        return status

    def _missing(self, spec: ExtensionSpec) -> list[str]:
        return [
            name for name in spec.dependencies if not self._loaded(self.specs[name])
        ]

    def selected_specs(
        self, *, enabled: set[str], disabled: set[str], extra: set[str] | None = None
    ):
        # Resolve every name before mutation, so a typo cannot silently lose commands.
        selected = (
            {self.resolve(k).module for k in enabled}
            if enabled
            else {s.module for s in self.specs.values() if s.default_enabled}
        )
        selected.update(self.resolve(k).module for k in extra or ())
        explicitly_enabled = {
            self.resolve(k).module for k in enabled | (extra or set())
        }
        if explicitly_enabled:
            selected.update(
                spec.module
                for spec in self.specs.values()
                if selected.intersection(spec.selection_parents)
            )
        excluded = {self.resolve(k).module for k in disabled}
        for spec in self.specs.values():
            if spec.module not in explicitly_enabled and excluded.intersection(
                spec.selection_parents
            ):
                excluded.add(spec.module)
        return tuple(
            s
            for s in self.specs.values()
            if s.module in selected and s.module not in excluded
        )

    async def load_all(
        self, *, enabled: set[str], disabled: set[str], extra: set[str] | None = None
    ):
        selected = {
            s.module
            for s in self.selected_specs(
                enabled=enabled, disabled=disabled, extra=extra
            )
        }
        for spec in self.specs.values():
            if spec.module not in selected:
                self._status(spec, ExtensionState.DISABLED)
            else:
                await self.load(spec.key)
        return self.report

    async def load(self, key: str) -> ExtensionStatus:
        async with self._lock:
            spec = self.resolve(key)
            missing = self._missing(spec)
            missing_services = [
                key
                for key in spec.required_services
                if not self.bot.services.available(key)
            ]
            if missing_services:
                return self._status(
                    spec,
                    ExtensionState.BLOCKED,
                    "services: " + ", ".join(missing_services),
                )
            if missing:
                return self._status(
                    spec, ExtensionState.BLOCKED, "missing: " + ", ".join(missing)
                )
            if self._loaded(spec):
                return self._status(spec, ExtensionState.LOADED)
            try:
                # Prefix and slash name collisions are preflighted by discord.py
                # add_cog/add_command; a failed extension is rolled back atomically.
                await self.bot.load_extension(spec.module)
                if not self._loaded(spec):
                    await self.bot.unload_extension(spec.module)
                    return self._status(
                        spec, ExtensionState.FAILED, "expected Cog missing"
                    )
            except Exception as exc:  # noqa: BLE001
                original = getattr(exc, "original", exc)
                return self._status(
                    spec, ExtensionState.FAILED, type(original).__name__
                )
            return self._status(spec, ExtensionState.LOADED)

    def _dependents(self, spec: ExtensionSpec) -> list[str]:
        return [
            s.key
            for s in self.specs.values()
            if spec.module in s.dependencies and self._loaded(s)
        ]

    async def unload(self, key: str) -> ExtensionStatus:
        async with self._lock:
            spec = self.resolve(key)
            dependents = self._dependents(spec)
            if dependents:
                msg = "Unload dependent extensions first: " + ", ".join(dependents)
                raise ValueError(msg)
            if spec.module in self.bot.extensions:
                await self.bot.unload_extension(spec.module)
            return self._status(spec, ExtensionState.UNLOADED)

    async def reload(self, key: str) -> ExtensionStatus:
        async with self._lock:
            spec = self.resolve(key)
            if self._dependents(spec):
                msg = (
                    "Unload dependent extensions before reloading a legacy service Cog."
                )
                raise ValueError(msg)
            if not self._loaded(spec):
                msg = "Load this extension before reloading it."
                raise ValueError(msg)
            try:
                await self.bot.reload_extension(spec.module)
            except commands.ExtensionError as exc:
                # discord.py restores the previous module on registration failures.
                original = getattr(exc, "original", exc)
                state = (
                    ExtensionState.LOADED
                    if self._loaded(spec)
                    else ExtensionState.FAILED
                )
                return self._status(
                    spec, state, "reload rejected: " + type(original).__name__
                )
            if not self._loaded(spec):
                await self.bot.unload_extension(spec.module)
                return self._status(spec, ExtensionState.FAILED, "expected Cog missing")
            return self._status(spec, ExtensionState.LOADED)

    def assert_sync_safe(self):
        if self.bot.offline:
            msg = "Remote command registration is unavailable in offline mode."
            raise ValueError(msg)
        # Compare actual loaded registrations too; native unload/remove must not
        # bypass the guard simply because the last startup status said loaded.
        incomplete = [
            s.key
            for s in self.specs.values()
            if not s.optional
            and not self._loaded(s)
            and (
                self.report.extensions.get(s.module) is None
                or self.report.extensions[s.module].state != ExtensionState.DISABLED
            )
        ]
        if incomplete or not self.report.complete:
            msg = "Command tree is incomplete; restore extensions before syncing."
            raise ValueError(msg)
