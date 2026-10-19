import contextlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Optional, TypeVar

import msgspec
from discord.ext import commands
from discord.ext.commands import Context
from discord.utils import escape_markdown as emd
from rapidfuzz import fuzz, process
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from chuni_penguin.adapters.utils import hydrate_records
from chuni_penguin.config import Config
from chuni_penguin.database import Alias, Chart, Cookie, Prefix, Song, UserConfig
from chuni_penguin.database import PersonalBest as DBPersonalBest
from chuni_penguin.repositories.chart_search import ChartSearchRepository
from chuni_penguin.repositories.folder_statistics import FolderStatisticsRepository
from chuni_penguin.repositories.linked_verse import LinkedVerseRepository
from chuni_penguin.repositories.recommendations import RecommendationRepository
from chuni_penguin.repositories.score_queries import ScoreQueryRepository
from chuni_penguin.types import (
    ChainLamp,
    ClearLamp,
    ComboLamp,
    Difficulty,
    Genre,
    Judgements,
    Rank,
    Score,
)
from chuni_penguin.types import Chart as NetworkChart
from chuni_penguin.types import PersonalBest as NetworkPersonalBest
from chuni_penguin.types import Song as NetworkSong
from chuni_penguin.utils import get_jacket_url

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService

T = TypeVar("T", bound=Score)


class CachedAlias:
    __slots__ = ("alias", "guild_id", "id", "song_id", "title")

    def __init__(
        self,
        id: Optional[int],
        alias: str,
        title: str,
        song_id: int,
        guild_id: int,
    ) -> None:
        self.id = id
        self.alias = alias
        self.title = title
        self.song_id = song_id
        self.guild_id = guild_id


def _legacy_alias_key(value: str) -> str:
    """Normalize persisted alias text without importing the learning service."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _describe_song_candidates(songs: Sequence[Song]) -> str:
    candidates = sorted({song.id: song for song in songs}.values(), key=lambda x: x.id)
    shown = candidates[:5]
    description = ", ".join(
        f"**{emd(song.title)}** (song ID {song.id})" for song in shown
    )
    if len(candidates) > len(shown):
        description += f", and {len(candidates) - len(shown)} more"
    return description


@dataclass(slots=True)
class SongSearchResult:
    songs: list[Song]
    matched_alias: Optional[Alias]
    similarity: float


class KeiyoushiUserAgents(msgspec.Struct):
    recommended: str
    desktop: list[str]
    mobile: list[str]


class AliasService:
    def __init__(self, database: "DatabaseService"):
        self.database = database
        self.alias_cache: dict[int, list[CachedAlias]] = {}

    async def reload(self) -> None:
        async with self.database.read_sessionmaker() as session:
            stmt = select(Song).options(joinedload(Song.aliases))
            songs = (await session.execute(stmt)).scalars().unique().all()

        self.alias_cache.clear()

        titles = set()
        global_aliases = self.alias_cache.setdefault(0, [])

        for song in songs:
            title_lower = song.title.lower()

            if title_lower not in titles:
                titles.add(title_lower)

                global_aliases.append(
                    CachedAlias(None, title_lower, song.title, song.id, 0)
                )

            for alias in song.aliases:
                guild_aliases = self.alias_cache.setdefault(alias.guild_id, [])

                guild_aliases.append(
                    CachedAlias(
                        alias.rowid,
                        alias.alias.lower(),
                        song.title,
                        alias.song_id,
                        alias.guild_id,
                    )
                )

        for song in songs:
            artist_lower = song.artist.lower()

            if artist_lower not in titles:
                titles.add(artist_lower)

                global_aliases.append(
                    CachedAlias(None, artist_lower, song.title, song.id, 0)
                )

    async def resolve_add_source(
        self,
        song_title_or_alias: str,
        added_alias: str,
        *,
        guild_id: int,
        global_alias: bool,
    ) -> Song:
        source_alias_key = _legacy_alias_key(song_title_or_alias)
        added_alias_key = _legacy_alias_key(added_alias)
        async with self.database.read_sessionmaker() as session:
            songs = (await session.execute(select(Song))).scalars().all()
            if any(_legacy_alias_key(song.title) == added_alias_key for song in songs):
                msg = f"**{emd(added_alias)}** is already a song title."
                raise commands.BadArgument(msg)

            # Limit to non-WE entries. WE entries are redirected to their
            # non-WE respectives when song-searching anyways.
            title_matches = [
                song
                for song in songs
                if song.id < 8000 and _legacy_alias_key(song.title) == source_alias_key
            ]
            if len(title_matches) > 1:
                msg = (
                    f"**{emd(song_title_or_alias)}** matches multiple songs: "
                    f"{_describe_song_candidates(title_matches)}. Use a unique "
                    "complete song title or an alias that identifies one song."
                )
                raise commands.BadArgument(msg)
            if title_matches:
                return title_matches[0]

            stmt = select(Alias).options(joinedload(Alias.song))
            if not global_alias:
                stmt = stmt.where(Alias.guild_id.in_((0, guild_id)))
            stmt = stmt.order_by(Alias.guild_id, Alias.rowid)
            alias_rows = (await session.execute(stmt)).scalars().all()
            matches = [
                alias
                for alias in alias_rows
                if _legacy_alias_key(alias.alias) == source_alias_key
            ]

            song_ids = {alias.song_id for alias in matches}
            if len(song_ids) > 1:
                msg = (
                    f"**{emd(song_title_or_alias)}** matches aliases for multiple "
                    f"songs: {_describe_song_candidates([alias.song for alias in matches])}. "
                    "Use a unique complete song title or an alias that identifies one song."
                )
                raise commands.BadArgument(msg)

            if not matches:
                msg = f"**{emd(song_title_or_alias)}** does not exist."
                raise commands.BadArgument(msg)

            # Several historical rows can point at the same song. Since this
            # lookup only needs the target song, those rows are equivalent.
            return matches[0].song

    async def global_conflicts(self, alias_lower: str) -> Sequence[Alias]:
        async with self.database.read_sessionmaker() as session:
            stmt = (
                select(Alias)
                .where(func.lower(Alias.alias) == alias_lower)
                .options(joinedload(Alias.song))
            )
            aliases = (await session.execute(stmt)).scalars().all()

        return aliases  # noqa: RET504 - preserve original read block

    async def guild_conflict(self, alias_lower: str, guild_id: int) -> Alias | None:
        async with self.database.read_sessionmaker() as session:
            stmt = (
                select(Alias)
                .where(Alias.guild_id.in_((0, guild_id)))
                .options(joinedload(Alias.song))
                .order_by(Alias.guild_id, Alias.rowid)
            )
            aliases = (await session.execute(stmt)).scalars().all()

        alias_key = _legacy_alias_key(alias_lower)
        matches = [
            alias for alias in aliases if _legacy_alias_key(alias.alias) == alias_key
        ]
        song_ids = {alias.song_id for alias in matches}
        if len(song_ids) > 1:
            msg = (
                f"Alias **{emd(alias_lower)}** is already attached to multiple songs: "
                f"{_describe_song_candidates([alias.song for alias in matches])}. "
                "Ask an alias manager to resolve the conflict."
            )
            raise commands.BadArgument(msg)

        alias_unit = matches[0] if matches else None

        return alias_unit  # noqa: RET504 - preserve original read block

    async def promote_to_global(self, aliases: Sequence[Alias], song_id: int) -> None:
        async def transaction(session: AsyncSession):
            aliases[0].guild_id = 0
            aliases[0].owner_id = None
            aliases[0].song_id = song_id
            await session.merge(aliases[0])

            for x in aliases[1:]:
                await session.delete(x)

        await self.database.writer.execute_fn(transaction)
        await self.reload()

    async def create_alias(
        self, added_alias: str, *, guild_id: int, song_id: int, owner_id: int | None
    ) -> None:
        await self.database.writer.add(
            Alias(
                alias=added_alias,
                guild_id=guild_id,
                song_id=song_id,
                owner_id=owner_id,
            )
        )
        await self.reload()

    async def removable_alias(
        self,
        alias_lower: str,
        *,
        is_alias_manager: bool,
        guild_id: int | None,
        author_id: int,
        bypass_ownership_check: bool,
    ) -> Alias | None:
        async with self.database.read_sessionmaker() as session:
            conditions = []

            if is_alias_manager:
                guild_condition = Alias.guild_id == 0

                if guild_id is not None:
                    guild_condition |= Alias.guild_id == guild_id

                conditions.append(guild_condition)
            elif guild_id is not None:
                conditions.append(Alias.guild_id == guild_id)

            if not bypass_ownership_check:
                conditions.append(Alias.owner_id == author_id)

            stmt = select(Alias).where(*conditions)
            stmt = stmt.order_by(Alias.guild_id, Alias.rowid)
            aliases = (await session.execute(stmt)).scalars().all()

        alias_key = _legacy_alias_key(alias_lower)
        matches = [
            alias for alias in aliases if _legacy_alias_key(alias.alias) == alias_key
        ]
        if len(matches) > 1:
            msg = (
                f"Multiple matching aliases are eligible for removal for "
                f"**{emd(alias_lower)}**. Ask an alias manager to resolve the "
                "duplicates before removing it."
            )
            raise commands.BadArgument(msg)

        alias = matches[0] if matches else None

        return alias  # noqa: RET504 - preserve the original query block

    async def for_song(self, song_id: int) -> Sequence[Alias]:
        async with self.database.read_sessionmaker() as session:
            stmt = select(Alias).where(Alias.song_id == song_id)
            aliases = (await session.execute(stmt)).scalars().all()

        return aliases  # noqa: RET504 - preserve the original query block

    async def delete_alias(self, alias: Alias) -> None:
        await self.database.writer.delete(alias)
        await self.reload()


@dataclass(frozen=True, slots=True)
class AccountBadges:
    is_supporter: bool
    is_contributor: bool


class UserSettingsService:
    def __init__(
        self, database: "DatabaseService", settings: Config, prefixes: dict[int, str]
    ):
        self.database = database
        self.settings = settings
        self.prefixes = prefixes

    async def guild_prefix(self, ctx: Context) -> str:
        default_prefix: str = self.settings.bot.default_prefix
        if ctx.guild is None:
            return default_prefix

        return self.prefixes.get(ctx.guild.id, default_prefix)

    async def set_guild_prefix(self, guild_id: int, new_prefix: str) -> None:
        default_prefix: str = self.settings.bot.default_prefix

        if new_prefix == default_prefix:
            stmt = delete(Prefix).where(Prefix.guild_id == guild_id)
            await self.database.writer.execute(stmt)

            with contextlib.suppress(KeyError):
                del self.prefixes[guild_id]
        else:
            prefix = Prefix(guild_id=guild_id, prefix=new_prefix)
            await self.database.writer.merge(prefix)
            self.prefixes[guild_id] = new_prefix

    def _get_not_logged_in_message(
        self,
        network: Literal["kamaitachi", "chuninet"] | None,
        author_id: int,
        target_id: int,
        *,
        is_interaction: bool,
    ):
        if network == "kamaitachi":
            network_name = " to Kamaitachi"
            command_name = "kamaitachi link"
        elif network == "chuninet":
            network_name = " to CHUNITHM-NET"
            command_name = "login"
        elif network is None:
            network_name = ""
            command_name = "login"
        else:
            msg = f"Unknown network: {network}"
            raise ValueError(msg)

        if author_id == target_id:
            return f"You are not logged in{network_name}. Please send `{'/' if is_interaction else self.settings.bot.default_prefix}{command_name}` in my DMs to log in."

        return f"<@{target_id}> is not logged in{network_name}."

    async def login_check(
        self,
        author_id: int,
        target_id: int | None = None,
        *,
        is_interaction: bool = False,
    ) -> str:
        target_id = target_id or author_id
        clal = await self.fetch_cookie(target_id)
        user_config = await self.fetch_user_config(target_id)

        if clal is None or (user_config.privacy_mode and author_id != target_id):
            msg = self._get_not_logged_in_message(
                "chuninet", author_id, target_id, is_interaction=is_interaction
            )
            raise commands.CommandError(msg)

        return clal

    async def stored_user_config(self, id: int) -> UserConfig | None:
        """Read a stored row without replacing absence with default settings."""
        async with self.database.read_sessionmaker() as session:
            stmt = select(UserConfig).where(UserConfig.discord_id == id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def fetch_user_config(self, id: int) -> UserConfig:
        return await self.stored_user_config(id) or UserConfig(
            discord_id=id, synthesis_alt_jacket="default", privacy_mode=False
        )

    async def save_user_config(self, user_config: UserConfig, *, is_new: bool) -> None:
        """Await the existing serialized writer, preserving add/merge semantics."""
        if is_new:
            await self.database.writer.add(user_config)
        else:
            await self.database.writer.merge(user_config)

    async def fetch_cookie(self, id: int) -> str | None:
        async with self.database.read_sessionmaker() as session:
            stmt = select(Cookie).where(Cookie.discord_id == id)
            cookie = (await session.execute(stmt)).scalar_one_or_none()

        if cookie is None or not cookie.cookie.startswith("#LWP-Cookies-2.0"):
            return None

        return cookie.cookie

    async def account_badges(self, id: int) -> AccountBadges:
        """Read the original local profile flags without returning credentials."""
        async with self.database.read_sessionmaker() as session:
            query = select(Cookie).where(Cookie.discord_id == id)
            cookie = (await session.execute(query)).scalar_one_or_none()

        return AccountBadges(
            is_supporter=cookie is not None and cookie.is_supporter,
            is_contributor=cookie is not None and cookie.is_contributor,
        )


class SongSearchService:
    def __init__(self, database: "DatabaseService", aliases: AliasService):
        self.database = database
        self.aliases = aliases

    async def find_song(
        self,
        query: str,
        *,
        guild_id: Optional[int] = None,
        worlds_end: bool = False,
    ) -> tuple[Song | None, Alias | None, float]:
        """Finds the song that best matches a given query.

        Parameters
        ----------
        query: str
            The query to search for.
        guild_id: Optional[int]
            The ID of the guild to search for aliases in. If None, only global aliases are searched.
        worlds_end: bool
            Whether to search for WORLD'S END charts, instead of normal charts.

        Returns
        -------
        tuple[Song, Alias | None, float]
            The third item is the similarity of the matched song.
        """
        aliases = self.aliases.alias_cache[0][:]

        if (
            guild_id is not None
            and (guild_aliases := self.aliases.alias_cache.get(guild_id)) is not None
        ):
            aliases.extend(guild_aliases)

        (_, similarity, index) = process.extractOne(
            query.lower(), [x.alias for x in aliases], scorer=fuzz.QRatio
        )
        matching_alias = aliases[index]

        async with self.database.read_sessionmaker() as session:
            condition = Song.id == matching_alias.song_id

            if worlds_end:
                condition = (Song.title == matching_alias.title) & (
                    Song.genre == "WORLD'S END"
                )

            stmt = select(Song).where(condition)
            song = (await session.execute(stmt)).scalar_one_or_none()

        if matching_alias.id is not None:
            stmt = (
                update(Alias)
                .where(Alias.rowid == matching_alias.id)
                .values(uses=Alias.uses + 1)
                .returning(Alias)
            )
            alias = (await self.database.writer.execute(stmt)).scalar_one()
        else:
            alias = None

        return song, alias, similarity

    async def find_songs(
        self,
        query: str,
        *,
        guild_id: Optional[int] = None,
        available: Optional[bool] = None,
        load_charts: bool = False,
        load_global_aliases: bool = False,
    ) -> SongSearchResult:
        aliases = self.aliases.alias_cache[0][:]

        if (
            guild_id is not None
            and (guild_aliases := self.aliases.alias_cache.get(guild_id)) is not None
        ):
            aliases.extend(guild_aliases)

        (_, similarity, index) = process.extractOne(
            query.lower(), [x.alias for x in aliases], scorer=fuzz.QRatio
        )
        matching_alias = aliases[index]

        async with self.database.read_sessionmaker() as session:
            cond = Song.title == matching_alias.title

            if available is not None:
                cond &= Song.available == available

            stmt = select(Song).where(cond)

            if load_charts:
                stmt = stmt.options(joinedload(Song.charts))

            if load_global_aliases:
                stmt = stmt.outerjoin(
                    Alias, (Alias.song_id == Song.id) & (Alias.guild_id == 0)
                ).options(contains_eager(Song.aliases))

            songs = (await session.execute(stmt)).scalars().unique()

        if matching_alias.id is not None:
            stmt = (
                update(Alias)
                .where(Alias.rowid == matching_alias.id)
                .values(uses=Alias.uses + 1)
                .returning(Alias)
            )
            alias = (await self.database.writer.execute(stmt)).scalar_one_or_none()
        else:
            alias = None

        return SongSearchResult(
            songs=list(songs), matched_alias=alias, similarity=similarity
        )


class ScoreConversionService:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def convert_to_network_pb(self, db_pb: DBPersonalBest):
        async with self.database.read_sessionmaker() as session:
            query = (
                select(Chart)
                .where(
                    (Chart.song_id == db_pb.song_id)
                    & (Chart.difficulty == db_pb.difficulty)
                )
                .options(joinedload(Chart.song))
            )
            chart = (await session.execute(query)).scalar_one()

        network_song = NetworkSong(
            id=chart.song.id,
            title=chart.song.title,
            version=chart.song.version,
            genre=Genre(chart.song.chunithm_catcode),
            jacket_url=get_jacket_url(chart.song),
        )
        network_chart = NetworkChart(
            difficulty=Difficulty(chart.difficulty),
            level=chart.level,
            internal_level=chart.const,
            max_combo=chart.maxcombo,
        )
        pb = NetworkPersonalBest(
            song=network_song,
            chart=network_chart,
            score=db_pb.score,
            rank=Rank.from_score(db_pb.score),
            clear_lamp=ClearLamp(db_pb.clear_lamp),
            combo_lamp=ComboLamp(db_pb.combo_lamp),
            chain_lamp=(
                ChainLamp(db_pb.chain_lamp)
                if db_pb.chain_lamp is not None
                else ChainLamp.none
            ),
            achieved_at=db_pb.achieved_at,
            max_combo=db_pb.max_combo,
            judgements=(
                Judgements(
                    justice_critical=db_pb.justice_critical,
                    justice=db_pb.justice,
                    attack=db_pb.attack,
                    miss=db_pb.miss,
                )
                if db_pb.justice_critical is not None
                and db_pb.justice is not None
                and db_pb.attack is not None
                and db_pb.miss is not None
                else None
            ),
            rating=Decimal(db_pb.rating) / 100 if db_pb.rating is not None else None,
            overpower=(
                Decimal(db_pb.overpower) / 1000 if db_pb.overpower is not None else None
            ),
        )
        return (await hydrate_records(self.database, [pb]))[0]


class UtilityServices:
    """Temporary typed facade for legacy callers; each responsibility has an owner."""

    def __init__(self, database: "DatabaseService", users: UserSettingsService):
        self.aliases = AliasService(database)
        self.search = SongSearchService(database, self.aliases)
        self.scores = ScoreConversionService(database)
        self.recommendations = RecommendationRepository(database)
        self.chart_search = ChartSearchRepository(database)
        self.score_queries = ScoreQueryRepository(database)
        self.folder_statistics = FolderStatisticsRepository(database)
        self.linked_verse = LinkedVerseRepository(database)
        self.users = users

    @property
    def alias_cache(self):
        return self.aliases.alias_cache

    @property
    def reload_alias_cache(self):
        return self.aliases.reload

    @property
    def find_song(self):
        return self.search.find_song

    @property
    def find_songs(self):
        return self.search.find_songs

    @property
    def convert_to_network_pb(self):
        return self.scores.convert_to_network_pb

    @property
    def fetch_user_config(self):
        return self.users.fetch_user_config

    @property
    def fetch_cookie(self):
        return self.users.fetch_cookie

    @property
    def guild_prefix(self):
        return self.users.guild_prefix

    @property
    def login_check(self):
        return self.users.login_check
