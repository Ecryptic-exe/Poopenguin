from types import SimpleNamespace

import pytest
import pytest_asyncio

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import Alias, Song
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.services.song_discovery import (
    SongDiscoveryService,
    abbreviation_score,
)


@pytest_asyncio.fixture
async def discovery(tmp_path):
    url = await create_fixture_database(tmp_path / "discovery.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        for song_id, title in (
            (1, "World End Dancehall"),
            (2, "World End Dancer"),
            (3, "Independent Song"),
        ):
            await database.writer.add(
                Song(
                    id=song_id,
                    title=title,
                    artist=f"Artist {song_id}",
                    chunithm_catcode=0,
                    genre="ORIGINAL",
                    version="X-VERSE",
                    available=True,
                    removed=False,
                )
            )
        aliases = AliasService(database)
        await aliases.reload()
        yield SimpleNamespace(
            database=database,
            aliases=aliases,
            service=SongDiscoveryService(database, aliases),
        )
    finally:
        await database.close()


def test_abbreviation_scoring_supports_initials_and_unicode():
    assert abbreviation_score("wed", "World's End Dancehall") >= 96
    assert abbreviation_score("ＷＥＤ", "World End Dancehall") >= 96
    assert abbreviation_score("world end dancehall", "World End Dancehall") == 100


@pytest.mark.asyncio
async def test_short_search_near_ties_require_selection(discovery):
    result = await discovery.service.search("wed", guild_id=33)
    assert result.needs_choice
    assert {candidate.song.id for candidate in result.candidates} == {1, 2}
    exact = await discovery.service.search("Ｉｎｄｅｐｅｎｄｅｎｔ　Ｓｏｎｇ")
    assert not exact.needs_choice and exact.candidates[0].song.id == 3


@pytest.mark.asyncio
async def test_duplicate_aliases_keep_all_song_candidates_without_writing(discovery):
    for song_id in (1, 1, 2):
        await discovery.database.writer.add(
            Alias(alias="shared", song_id=song_id, guild_id=33, owner_id=11)
        )
    await discovery.aliases.reload()
    result = await discovery.service.search("SHARED", guild_id=33)
    assert result.exact_alias and result.needs_choice
    assert {candidate.song.id for candidate in result.candidates} == {1, 2}
    async with discovery.database.read_sessionmaker() as session:
        from sqlalchemy import select

        assert all(alias.uses == 0 for alias in await session.scalars(select(Alias)))
    outside = await discovery.service.search("shared", guild_id=44)
    assert not outside.exact_alias


@pytest.mark.asyncio
async def test_correction_excludes_current_song_and_bypasses_alias(discovery):
    await discovery.database.writer.add(
        Alias(alias="world end dancehall", song_id=3, guild_id=33, owner_id=11)
    )
    await discovery.aliases.reload()
    ambiguous = await discovery.service.search("World End Dancehall", guild_id=33)
    assert ambiguous.needs_choice
    fixed = await discovery.service.search(
        "World End Dancehall", guild_id=33, ignore_exact=True, exclude_song_id=1
    )
    assert fixed.needs_choice
    assert 1 not in {candidate.song.id for candidate in fixed.candidates}
    assert fixed.candidates[0].song.id == 2


@pytest.mark.asyncio
async def test_empty_or_unrelated_search_has_recovery_result(discovery):
    with pytest.raises(ValueError):
        await discovery.service.search("  ")
    assert not (await discovery.service.search("完全無關測試字串")).candidates


@pytest.mark.asyncio
async def test_exact_alias_does_not_expand_to_other_artist_with_same_title(discovery):
    await discovery.database.writer.add(
        Song(
            id=11,
            title="Independent Song",
            artist="Different Composer",
            chunithm_catcode=0,
            genre="ORIGINAL",
            version="X-VERSE",
            available=True,
            removed=False,
        )
    )
    await discovery.database.writer.add(
        Alias(
            alias="independent",
            song_id=3,
            guild_id=33,
            owner_id=11,
        )
    )
    await discovery.aliases.reload()
    found = await discovery.service.search("independent", guild_id=33)
    assert not found.needs_choice and [
        candidate.song.id for candidate in found.candidates
    ] == [3]
