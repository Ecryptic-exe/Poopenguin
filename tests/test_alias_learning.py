from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import Alias, Song
from chuni_penguin.services.alias_learning import (
    AliasLearningService,
    normalize_alias,
    normalize_alias_display,
)
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.database import DatabaseService


@pytest_asyncio.fixture
async def database(tmp_path):
    url = await create_fixture_database(tmp_path / "alias-learning.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        yield database
    finally:
        await database.close()


async def add_song(database, song_id: int, title: str) -> None:
    await database.writer.add(
        Song(
            id=song_id,
            title=title,
            artist=f"Artist {song_id}",
            chunithm_catcode=0,
            genre="ORIGINAL",
            version="X-VERSE-X",
            available=True,
            removed=False,
        )
    )


def make_service(database):
    aliases = AliasService(database)
    return AliasLearningService(database, aliases), aliases


def test_alias_normalization_uses_nfkc_casefold_and_collapsed_whitespace():
    fullwidth_alpha_beta = "  \uff21\uff4c\uff50\uff48\uff41\u3000 \uff22\uff45\uff54\uff41  "
    assert normalize_alias_display(fullwidth_alpha_beta) == "Alpha Beta"
    assert normalize_alias(fullwidth_alpha_beta) == "alpha beta"
    assert normalize_alias("Straße") == "strasse"


@pytest.mark.parametrize(
    "alias_text",
    [
        "",
        "   ",
        "line\nbreak",
        "@everyone",
        "@Here",
        "<@123>",
        "x" * 101,
    ],
)
def test_alias_normalization_rejects_unsafe_or_invalid_input(alias_text):
    with pytest.raises(ValueError):
        normalize_alias_display(alias_text)


def test_alias_normalization_requires_text():
    with pytest.raises(TypeError):
        normalize_alias_display(None)


@pytest.mark.asyncio
async def test_inspect_finds_all_global_and_guild_duplicates_without_scalar_error(
    database,
):
    await add_song(database, 900000002, "Second fixture")
    await add_song(database, 900000003, "Third fixture")
    await database.writer.add(
        Alias(alias="Short Cut", guild_id=0, song_id=900000002, owner_id=21)
    )
    await database.writer.add(
        Alias(alias="short   cut", guild_id=1234, song_id=900000003, owner_id=22)
    )
    service, _ = make_service(database)

    result = await service.inspect(
        "\uff33\uff28\uff2f\uff32\uff34 cut", guild_id=1234, song_id=900000001
    )

    assert result.status == "conflict"
    assert result.alias == "SHORT cut"
    assert result.song_id == 900000001
    assert result.title == "Offline fixture"
    assert result.guild_id == 1234
    assert [(row.guild_id, row.song_id) for row in result.conflicts] == [
        (0, 900000002),
        (1234, 900000003),
    ]
    assert all(row.rowid is not None for row in result.conflicts)


@pytest.mark.asyncio
async def test_inspect_ignores_other_guild_aliases_but_checks_global_aliases(database):
    await add_song(database, 900000002, "Second fixture")
    await database.writer.add(
        Alias(alias="Local shortcut", guild_id=2222, song_id=900000002, owner_id=21)
    )
    service, _ = make_service(database)

    unrelated_guild = await service.inspect(
        "local shortcut", guild_id=1111, song_id=900000001
    )
    assert unrelated_guild.status == "available"

    await database.writer.add(
        Alias(alias="LOCAL SHORTCUT", guild_id=0, song_id=900000002, owner_id=None)
    )
    global_collision = await service.inspect(
        "local shortcut", guild_id=1111, song_id=900000001
    )
    assert global_collision.status == "conflict"
    assert [row.guild_id for row in global_collision.conflicts] == [0]


@pytest.mark.asyncio
async def test_real_unicode_song_title_is_a_title_conflict(database):
    unicode_title = "  \uff23\uff41\uff46\u00e9\u3000Song  "
    await add_song(database, 900000002, unicode_title)
    service, _ = make_service(database)

    result = await service.inspect("café song", guild_id=1234, song_id=900000001)

    assert result.status == "title_conflict"
    assert len(result.conflicts) == 1
    assert result.conflicts[0].rowid is None
    assert result.conflicts[0].song_id == 900000002
    assert result.conflicts[0].title == unicode_title
    assert result.conflicts[0].guild_id == 0


@pytest.mark.asyncio
async def test_same_song_alias_is_idempotent_and_preserves_existing_owner(database):
    await database.writer.add(
        Alias(alias="My Short Name", guild_id=1234, song_id=900000001, owner_id=77)
    )
    await database.writer.add(
        Alias(alias="my short name", guild_id=1234, song_id=900000001, owner_id=70)
    )
    service, aliases = make_service(database)

    inspected = await service.inspect(
        "MY SHORT NAME", guild_id=1234, song_id=900000001
    )
    confirmed = await service.confirm(
        "my short name",
        guild_id=1234,
        song_id=900000001,
        owner_id=88,
    )

    assert inspected.status == confirmed.status == "already_exists"
    assert len(confirmed.conflicts) == 2
    assert aliases.alias_cache == {}
    async with database.read_sessionmaker() as session:
        saved = (
            await session.execute(
                select(Alias).where(Alias.guild_id == 1234, Alias.song_id == 900000001)
            )
        ).scalars().all()
    assert len(saved) == 2
    assert {row.alias for row in saved} == {"My Short Name", "my short name"}
    assert {row.owner_id for row in saved} == {70, 77}


@pytest.mark.asyncio
async def test_confirm_adds_guild_alias_with_owner_and_reloads_cache(database):
    service, aliases = make_service(database)

    result = await service.confirm(
        "  \uff2e\uff45\uff57   Shortcut ",
        guild_id=1234,
        song_id=900000001,
        owner_id=4567,
    )

    assert result.status == "added"
    assert result.alias == "New Shortcut"
    assert result.conflicts == []
    assert aliases.alias_cache[1234][0].alias == "new shortcut"
    async with database.read_sessionmaker() as session:
        saved = (
            await session.execute(select(Alias).where(Alias.alias == "New Shortcut"))
        ).scalar_one()
    assert saved.guild_id == 1234
    assert saved.song_id == 900000001
    assert saved.owner_id == 4567


@pytest.mark.asyncio
async def test_confirm_rechecks_conflicts_inside_write_transaction(database):
    await add_song(database, 900000002, "Second fixture")
    service, aliases = make_service(database)
    inspected = await service.inspect(
        "racy alias", guild_id=1234, song_id=900000001
    )
    assert inspected.status == "available"

    await database.writer.add(
        Alias(alias="Racy Alias", guild_id=1234, song_id=900000002, owner_id=33)
    )
    confirmed = await service.confirm(
        "racy alias", guild_id=1234, song_id=900000001, owner_id=44
    )

    assert confirmed.status == "conflict"
    assert aliases.alias_cache == {}
    async with database.read_sessionmaker() as session:
        rows = (
            await session.execute(select(Alias).where(Alias.alias.ilike("racy alias")))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].song_id == 900000002
    assert rows[0].owner_id == 33


@pytest.mark.asyncio
async def test_concurrent_confirms_for_different_songs_add_only_one_alias(database):
    await add_song(database, 900000002, "Second fixture")
    service, _ = make_service(database)

    results = await asyncio.gather(
        service.confirm(
            "shared shorthand", guild_id=1234, song_id=900000001, owner_id=41
        ),
        service.confirm(
            "SHARED SHORTHAND", guild_id=1234, song_id=900000002, owner_id=42
        ),
    )

    assert sorted(result.status for result in results) == ["added", "conflict"]
    async with database.read_sessionmaker() as session:
        rows = (
            await session.execute(select(Alias).where(Alias.guild_id == 1234))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].song_id in {900000001, 900000002}
    assert rows[0].owner_id in {41, 42}


@pytest.mark.asyncio
async def test_rejects_invalid_scope_ids_and_missing_songs(database):
    service, _ = make_service(database)

    for invalid_guild in (0, -1, True, "1234"):
        with pytest.raises(ValueError):
            await service.inspect(
                "short", guild_id=invalid_guild, song_id=900000001
            )
    for invalid_owner in (0, -1, True, "5"):
        with pytest.raises(ValueError):
            await service.confirm(
                "short",
                guild_id=1234,
                song_id=900000001,
                owner_id=invalid_owner,
            )
    for invalid_song in (0, -1, True, "900000001"):
        with pytest.raises(ValueError):
            await service.inspect("short", guild_id=1234, song_id=invalid_song)

    with pytest.raises(ValueError, match="Song does not exist"):
        await service.inspect("short", guild_id=1234, song_id=123456789)
    with pytest.raises(ValueError, match="Song does not exist"):
        await service.confirm(
            "short", guild_id=1234, song_id=123456789, owner_id=5
        )
