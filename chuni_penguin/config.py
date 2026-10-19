import contextlib
import os
from collections.abc import Iterator, Mapping
from configparser import ConfigParser
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast, overload

import msgspec

if TYPE_CHECKING:
    from chuni_penguin.types import Rank

T = TypeVar("T")


class CommaDelimitedSet(set[T], Generic[T]):
    pass


class BotConfig(msgspec.Struct):
    token: str = ""
    default_prefix: str = "c>"
    db_connection_string: str = "sqlite+aiosqlite:///data/database.sqlite3"
    error_reporting_webhook: str | None = None
    alias_managers: CommaDelimitedSet[int] = msgspec.field(
        default_factory=CommaDelimitedSet
    )
    support_server_invite: str | None = None


class WebConfig(msgspec.Struct):
    enable: bool = False
    listen_address: str = "127.0.0.1"
    port: int = 5730
    base_url: str | None = None
    goatcounter: str | None = None
    serve_assets: bool = False
    fallback_url: str | None = None
    trust_proxy: bool = False
    is_accessible: bool = False

    def __post_init__(self):
        self.is_accessible = (
            self.enable
            and self.base_url is not None
            and "127.0.0.1" not in self.base_url
            and "localhost" not in self.base_url
        )


class CredentialsConfig(msgspec.Struct):
    chunirec_token: str | None = None
    kamaitachi_client_id: str | None = None
    kamaitachi_client_secret: str | None = None
    kamaitachi_api_key: str | None = None
    goatcounter_api_key: str | None = None
    sega_id_username: str | None = None
    sega_id_password: str | None = None
    kofi_verification_token: str | None = None
    git_auth_username: str | None = None
    git_auth_password: str | None = None


class IconsConfig(msgspec.Struct):
    sssp: str | None = None
    sss: str | None = None
    ssp: str | None = None
    ss: str | None = None
    sp: str | None = None
    s: str | None = None
    aaa: str | None = None
    aa: str | None = None
    a: str | None = None
    bbb: str | None = None
    bb: str | None = None
    b: str | None = None
    c: str | None = None
    d: str | None = None
    bonus_icon_map: str | None = None
    bonus_icon_exp: str | None = None
    bonus_icon_point: str | None = None
    bonus_icon_chance: str | None = None
    bonus_icon_critical: str | None = None
    bonus_icon_gamepoint: str | None = None
    bonus_icon_selection: str | None = None
    linked_gate_not_found: str | None = None
    linked_gate_under_analysis: str | None = None
    linked_gate_origin_linkable: str | None = None
    linked_gate_air_linkable: str | None = None
    linked_gate_star_linkable: str | None = None
    linked_gate_amazon_linkable: str | None = None
    linked_gate_crystal_linkable: str | None = None
    linked_gate_paradise_linkable: str | None = None
    linked_gate_new_linkable: str | None = None
    linked_gate_sun_linkable: str | None = None
    linked_gate_luminous_linkable: str | None = None
    linked_gate_verse_linkable: str | None = None
    linked_gate_origin_clear: str | None = None
    linked_gate_air_clear: str | None = None
    linked_gate_star_clear: str | None = None
    linked_gate_amazon_clear: str | None = None
    linked_gate_crystal_clear: str | None = None
    linked_gate_paradise_clear: str | None = None
    linked_gate_new_clear: str | None = None
    linked_gate_sun_clear: str | None = None
    linked_gate_luminous_clear: str | None = None
    linked_gate_verse_clear: str | None = None
    link_level_infinity: str | None = None
    link_level_v: str | None = None
    link_level_iv: str | None = None
    link_level_iii: str | None = None
    link_level_ii: str | None = None
    link_level_i: str | None = None
    ranking_delta_down: str | None = None
    ranking_delta_keep: str | None = None
    ranking_delta_up: str | None = None

    @overload
    def icon(self, named: str) -> str | None: ...

    @overload
    def icon(self, named: str, fallback: str) -> str: ...

    def icon(self, named: str, fallback: str | None = None) -> str | None:
        try:
            return getattr(self, named) or fallback
        except AttributeError:
            return fallback

    def rank_icon(self, rank: "str | Rank") -> str:
        return self.icon(str(rank).lower().replace("+", "p"), str(rank))


class LegalConfig(msgspec.Struct):
    privacy_policy: str | None = None
    terms_of_service: str | None = None


class DangerousConfig(msgspec.Struct):
    dev: bool = False
    debug_totp: str | None = None


class CogsConfig(msgspec.Struct):
    # Empty enabled means the existing default list. Names are module paths or keys.
    enabled: CommaDelimitedSet[str] = msgspec.field(default_factory=CommaDelimitedSet)
    disabled: CommaDelimitedSet[str] = msgspec.field(default_factory=CommaDelimitedSet)
    # Add opt-in domains without copying the entire default whitelist.
    extra: CommaDelimitedSet[str] = msgspec.field(default_factory=CommaDelimitedSet)


class ProvidersConfig(msgspec.Struct):
    # Opt-in, ordered list. Existing command routing is unchanged by this setting.
    enabled: str = ""
    chunilib_snapshot_path: Path | None = None
    chunilib_game_version: str = "crossversex"
    timeout_seconds: float = 10


class RatingConfig(msgspec.Struct):
    # Trusted local C checkout; no import until a rating Cog is selected.
    engine_root: Path | None = None
    catalog_provider: str = "chunilib_snapshot"
    associations: Path | None = None
    statistics_root: Path | None = None
    min_players: int = 100
    max_workers: int = 1
    queue_limit: int = 8
    cache_ttl: int = 86400
    worker_timeout: int = 180


class FunConfig(msgspec.Struct):
    language: Literal["english", "chinese"] = "english"
    chinese_guilds: CommaDelimitedSet[int] = msgspec.field(
        default_factory=CommaDelimitedSet
    )
    english_guilds: CommaDelimitedSet[int] = msgspec.field(
        default_factory=CommaDelimitedSet
    )


class CopypastaConfig(msgspec.Struct):
    # Existing private working copy; no implicit B source or seed import.
    file: Path | None = None


class KnowledgeConfig(msgspec.Struct):
    manual_file: Path | None = None
    bindings_file: Path | None = None
    semantic_root: Path | None = None
    semantic_catalogue_id: str | None = None


class LocalSeedsConfig(msgspec.Struct, tag="local"):
    path: Path


class GitSeedsConfig(msgspec.Struct, tag="git"):
    url: str
    username: str
    email: str
    branch: str | msgspec.UnsetType = msgspec.UNSET


def config_dec_hook(ty: type, obj: Any):
    # Handle generic types - these have __args__ for parameters and __origin__ for the
    # original type
    try:
        ty_arg = ty.__args__[0] if len(ty.__args__) > 0 else None
        ty = ty.__origin__
    except AttributeError:
        ty_arg = None

    if ty is CommaDelimitedSet and ty_arg in (int, str) and isinstance(obj, str):
        return CommaDelimitedSet(
            ty_arg(item.strip()) for item in obj.split(",") if item.strip()
        )

    if issubclass(ty, Path) and isinstance(obj, str):
        return ty(obj)

    msg = f"Objects of type {obj.__class__.__name__} cannot be converted into type {ty}"
    raise NotImplementedError(msg)


class Config:
    __slots__ = (
        "_path",
        "bot",
        "cogs",
        "copypasta",
        "credentials",
        "dangerous",
        "fun",
        "icons",
        "knowledge",
        "legal",
        "providers",
        "rating",
        "seeds",
        "web",
    )

    def __init__(self, path: "str | Path") -> None:
        self._path = path
        self.reload()

    def reload(self):
        if self._path is None:
            msg = "In-memory configuration cannot be reloaded from a file."
            raise ValueError(msg)
        cfg = ConfigParser()
        if not cfg.read(self._path, encoding="utf-8-sig"):
            raise FileNotFoundError(self._path)
        self._load(dict(cfg))

    @classmethod
    def from_mapping(cls, sections: Mapping[str, Any]) -> "Config":
        """Build explicit settings without reading credentials or a deployment file."""
        settings = cls.__new__(cls)
        settings._path = None
        settings._load(sections)
        return settings

    def _load(self, cfg: Mapping[str, Any]):
        self.bot = msgspec.convert(
            cfg.get("bot", {}), type=BotConfig, strict=False, dec_hook=config_dec_hook
        )
        self.cogs = msgspec.convert(
            cfg.get("cogs", {}), type=CogsConfig, strict=False, dec_hook=config_dec_hook
        )
        self.providers = msgspec.convert(
            cfg.get("providers", {}),
            type=ProvidersConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.web = msgspec.convert(
            cfg.get("web", {}), type=WebConfig, strict=False, dec_hook=config_dec_hook
        )
        self.rating = msgspec.convert(
            cfg.get("rating", {}),
            type=RatingConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.fun = msgspec.convert(
            cfg.get("fun", {}), type=FunConfig, strict=False, dec_hook=config_dec_hook
        )
        self.copypasta = msgspec.convert(
            cfg.get("copypasta", {}),
            type=CopypastaConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.knowledge = msgspec.convert(
            cfg.get("knowledge", {}),
            type=KnowledgeConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.credentials = msgspec.convert(
            cfg.get("credentials", {}),
            type=CredentialsConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.icons = msgspec.convert(
            cfg.get("icons", {}),
            type=IconsConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.legal = msgspec.convert(
            cfg.get("legal", {}),
            type=LegalConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )
        self.dangerous = msgspec.convert(
            cfg.get("dangerous", {}),
            type=DangerousConfig,
            strict=False,
            dec_hook=config_dec_hook,
        )

        if "seeds" in cfg:
            self.seeds = msgspec.convert(
                cfg["seeds"],
                type=LocalSeedsConfig | GitSeedsConfig,
                strict=False,
                dec_hook=config_dec_hook,
            )
        else:
            self.seeds = LocalSeedsConfig(Path("chuni_penguin/database/seeds"))


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "bot.ini"
_settings_context: ContextVar[Config | None] = ContextVar(
    "chuni_settings", default=None
)
_default_settings: Config | None = None


def get_config() -> Config:
    settings = _settings_context.get()
    if settings is not None:
        return settings
    global _default_settings
    if _default_settings is None:
        explicit_path = os.environ.get("CHUNI_CONFIG_FILE")
        path = Path(explicit_path) if explicit_path else DEFAULT_CONFIG_PATH
        # Importing pure modules must not require a Discord token. The launcher
        # rejects empty tokens before connecting. Explicit missing paths still fail.
        _default_settings = (
            Config(path) if explicit_path or path.exists() else Config.from_mapping({})
        )
    return _default_settings


@contextlib.contextmanager
def use_config(settings: Config) -> Iterator[Config]:
    """Bind legacy imports to explicit settings for this async task and its children."""
    token = _settings_context.set(settings)
    try:
        yield settings
    finally:
        _settings_context.reset(token)


class _ConfigProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_config(), name)


# Keep all existing from-config imports pointing at the same object. No Cog
# migration is required to use isolated startup/test settings.
config = cast(Config, _ConfigProxy())
