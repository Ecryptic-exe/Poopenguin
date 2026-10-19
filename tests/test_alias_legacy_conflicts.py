from __future__ import annotations

import pytest
import pytest_asyncio
from discord.ext import commands
from sqlalchemy import select

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import Alias, Song
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.database import DatabaseService


@pytest_asyncio.fixture
async def database(tmp_path):
    url = await create_fixture_database(tmp_path / "alias-legacy.sqlite3")
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


async def add_alias(
    database,
    alias: str,
    *,
    guild_id: int,
    song_id: int,
    owner_id: int | None,
) -> None:
    await database.writer.add(
        Alias(
            alias=alias,
            guild_id=guild_id,
            song_id=song_id,
            owner_id=owner_id,
        )
    )


@pytest.mark.asyncio
async def test_source_resolution_deduplicates_same_song_and_respects_scope(database):
    await add_song(database, 101, "First fixture song")
    await add_song(database, 102, "Other guild song")
    await add_alias(
        database,
        "  Ｓｈｏｒｔ　 Cut ",
        guild_id=0,
        song_id=101,
        owner_id=None,
    )
    await add_alias(database, "short cut", guild_id=1234, song_id=101, owner_id=11)
    await add_alias(database, "SHORT CUT", guild_id=2222, song_id=102, owner_id=22)
    aliases = AliasService(database)

    resolved = await aliases.resolve_add_source(
        "ＳＨＯＲＴ cut", "brand new alias", guild_id=1234, global_alias=False
    )
    assert resolved.id == 101

    with pytest.raises(commands.BadArgument, match="Other guild song"):
        await aliases.resolve_add_source(
            "short cut", "brand new alias", guild_id=1234, global_alias=True
        )


@pytest.mark.asyncio
async def test_source_resolution_reports_distinct_alias_targets(database):
    await add_song(database, 111, "First target")
    await add_song(database, 112, "Second target")
    await add_alias(database, "shared name", guild_id=0, song_id=111, owner_id=None)
    await add_alias(database, "SHARED NAME", guild_id=1234, song_id=112, owner_id=12)
    aliases = AliasService(database)

    with pytest.raises(commands.BadArgument) as error:
        await aliases.resolve_add_source(
            "shared name", "new name", guild_id=1234, global_alias=False
        )

    message = str(error.value)
    assert "First target" in message
    assert "Second target" in message


@pytest.mark.asyncio
async def test_source_resolution_reports_duplicate_canonical_titles(database):
    await add_song(database, 121, "Duplicate title")
    await add_song(database, 122, "Ｄｕｐｌｉｃａｔｅ　title")
    aliases = AliasService(database)

    with pytest.raises(commands.BadArgument, match="multiple songs") as error:
        await aliases.resolve_add_source(
            "duplicate title", "new name", guild_id=1234, global_alias=False
        )

    assert "song ID 121" in str(error.value)
    assert "song ID 122" in str(error.value)


@pytest.mark.asyncio
async def test_guild_conflict_returns_deterministic_same_song_row(database):
    await add_song(database, 131, "One target")
    await add_song(database, 132, "Unrelated guild target")
    await add_alias(database, "Same shortcut", guild_id=1234, song_id=131, owner_id=13)
    await add_alias(database, "SAME SHORTCUT", guild_id=0, song_id=131, owner_id=None)
    await add_alias(database, "same shortcut", guild_id=2222, song_id=132, owner_id=22)
    aliases = AliasService(database)

    conflict = await aliases.guild_conflict("same shortcut", guild_id=1234)

    assert conflict is not None
    assert conflict.guild_id == 0
    assert conflict.song_id == 131


@pytest.mark.asyncio
async def test_guild_conflict_reports_multiple_targets(database):
    await add_song(database, 141, "Global target")
    await add_song(database, 142, "Guild target")
    await add_alias(database, "ambiguous", guild_id=0, song_id=141, owner_id=None)
    await add_alias(database, "Ambiguous", guild_id=1234, song_id=142, owner_id=14)
    aliases = AliasService(database)

    with pytest.raises(commands.BadArgument, match="multiple songs") as error:
        await aliases.guild_conflict("ambiguous", guild_id=1234)

    assert "Global target" in str(error.value)
    assert "Guild target" in str(error.value)


@pytest.mark.asyncio
async def test_removal_keeps_owner_and_guild_filters_and_refuses_duplicates(database):
    await add_song(database, 151, "Global alias song")
    await add_song(database, 152, "Local alias song")
    await add_song(database, 153, "Other guild alias song")
    await add_alias(database, "owned shortcut", guild_id=0, song_id=151, owner_id=51)
    await add_alias(database, "Owned Shortcut", guild_id=1234, song_id=152, owner_id=51)
    await add_alias(database, "owned shortcut", guild_id=2222, song_id=153, owner_id=51)
    aliases = AliasService(database)

    local = await aliases.removable_alias(
        "owned shortcut",
        is_alias_manager=False,
        guild_id=1234,
        author_id=51,
        bypass_ownership_check=False,
    )
    assert local is not None
    assert local.guild_id == 1234
    assert local.song_id == 152

    unauthorized = await aliases.removable_alias(
        "owned shortcut",
        is_alias_manager=False,
        guild_id=1234,
        author_id=99,
        bypass_ownership_check=False,
    )
    assert unauthorized is None

    await add_alias(database, "owned shortcut", guild_id=1234, song_id=151, owner_id=51)
    with pytest.raises(commands.BadArgument, match="(?i)ask an alias manager"):
        await aliases.removable_alias(
            "owned shortcut",
            is_alias_manager=False,
            guild_id=1234,
            author_id=51,
            bypass_ownership_check=False,
        )

    async with database.read_sessionmaker() as session:
        stored = (
            (
                await session.execute(
                    select(Alias).where(Alias.alias.ilike("owned shortcut"))
                )
            )
            .scalars()
            .all()
        )
    assert len(stored) == 4


@pytest.mark.asyncio
async def test_manager_removal_scope_is_global_and_current_guild(database):
    await add_song(database, 161, "Global target")
    await add_song(database, 162, "Current guild target")
    await add_song(database, 163, "Other guild target")
    await add_alias(database, "global shortcut", guild_id=0, song_id=161, owner_id=None)
    await add_alias(database, "local shortcut", guild_id=1234, song_id=162, owner_id=16)
    await add_alias(database, "local shortcut", guild_id=2222, song_id=163, owner_id=16)
    aliases = AliasService(database)

    global_alias = await aliases.removable_alias(
        "global shortcut",
        is_alias_manager=True,
        guild_id=1234,
        author_id=99,
        bypass_ownership_check=True,
    )
    current_guild_alias = await aliases.removable_alias(
        "local shortcut",
        is_alias_manager=True,
        guild_id=1234,
        author_id=99,
        bypass_ownership_check=True,
    )
    dm_global_alias = await aliases.removable_alias(
        "global shortcut",
        is_alias_manager=True,
        guild_id=None,
        author_id=99,
        bypass_ownership_check=True,
    )
    dm_guild_alias = await aliases.removable_alias(
        "local shortcut",
        is_alias_manager=True,
        guild_id=None,
        author_id=99,
        bypass_ownership_check=True,
    )

    assert global_alias is not None and global_alias.guild_id == 0
    assert current_guild_alias is not None and current_guild_alias.guild_id == 1234
    assert dm_global_alias is not None and dm_global_alias.guild_id == 0
    assert dm_guild_alias is None
