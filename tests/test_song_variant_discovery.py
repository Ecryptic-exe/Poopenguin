from types import SimpleNamespace

import pytest
import pytest_asyncio

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import Alias, Chart, Song
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.services.song_discovery import SongDiscoveryService


def song(song_id, title, artist, *, genre="ORIGINAL", available=True):
    return Song(
        id=song_id,
        title=title,
        artist=artist,
        chunithm_catcode=0,
        genre=genre,
        version="X-VERSE",
        available=available,
        removed=False,
    )


@pytest_asyncio.fixture
async def variant_discovery(tmp_path):
    url = await create_fixture_database(tmp_path / "song-variants.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        records = (
            song(1101, "Variant Song", "Variant Artist"),
            song(1102, "Variant Song", "Variant Artist", genre="WORLD'S END"),
            song(1103, "Variant Song", "Variant Artist", genre="WORLD'S END"),
            song(1104, "Variant Song", "Variant Artist", genre="WORLD'S END"),
            song(
                1111,
                "WE Only Song",
                "Only Artist",
                genre="WORLD'S END",
                available=False,
            ),
            song(1112, "WE Only Song", "Only Artist", genre="WORLD'S END"),
            song(1121, "Same Title", "First Artist"),
            song(1122, "Same Title", "Second Artist"),
            song(1131, "Repeated Composition", "Same Artist"),
            song(1132, "Repeated Composition", "Same Artist"),
            song(1141, "First Composition", "Composer One"),
            song(1142, "Second Composition", "Composer Two"),
            song(1151, "Alias Variant", "Alias Artist"),
            song(1152, "Alias Variant", "Alias Artist", genre="WORLD'S END"),
            song(1161, "Correctable Song", "Original Artist"),
            song(1162, "Correctable Song", "Original Artist", genre="WORLD'S END"),
            song(1163, "Correctable Song", "Original Artist", genre="WORLD'S END"),
            song(1164, "Correctable Song", "Other Artist"),
        )
        for record in records:
            await database.writer.add(record)
            await database.writer.add(
                Chart(
                    song_id=record.id,
                    difficulty="MAS",
                    level="14",
                    const=14.0,
                    available=True,
                )
            )

        for alias, song_id in (
            ("shared composition alias", 1141),
            ("shared composition alias", 1142),
            ("alias points to this WE chart", 1152),
        ):
            await database.writer.add(
                Alias(alias=alias, song_id=song_id, guild_id=33, owner_id=11)
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


@pytest.mark.asyncio
async def test_normal_and_multiple_worlds_end_charts_make_one_candidate(
    variant_discovery,
):
    result = await variant_discovery.service.search("Variant Song")

    assert not result.needs_choice
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.song.id == 1101
    assert {variant.id for variant in candidate.variants} == {1102, 1103, 1104}
    assert {variant.id for variant in candidate.songs} == {1101, 1102, 1103, 1104}
    assert all(
        [chart.difficulty for chart in variant.charts] == ["MAS"]
        for variant in candidate.songs
    )


@pytest.mark.asyncio
async def test_worlds_end_only_group_prefers_available_representative(
    variant_discovery,
):
    result = await variant_discovery.service.search("WE Only Song")

    assert not result.needs_choice
    candidate = result.candidates[0]
    assert candidate.song.id == 1112
    assert candidate.songs[1].id == 1111


@pytest.mark.asyncio
async def test_same_title_with_different_artists_remains_ambiguous(variant_discovery):
    result = await variant_discovery.service.search("Same Title")

    assert result.needs_choice
    assert {candidate.song.artist for candidate in result.candidates} == {
        "First Artist",
        "Second Artist",
    }
    assert all(candidate.songs == (candidate.song,) for candidate in result.candidates)


@pytest.mark.asyncio
async def test_multiple_non_we_rows_for_same_artist_stay_separate(variant_discovery):
    result = await variant_discovery.service.search("Repeated Composition")

    assert result.needs_choice
    assert {candidate.song.id for candidate in result.candidates} == {1131, 1132}
    assert all(candidate.songs == (candidate.song,) for candidate in result.candidates)


@pytest.mark.asyncio
async def test_duplicate_alias_for_different_compositions_still_requires_choice(
    variant_discovery,
):
    result = await variant_discovery.service.search(
        "shared composition alias", guild_id=33
    )

    assert result.exact_alias
    assert result.needs_choice
    assert {candidate.song.id for candidate in result.candidates} == {1141, 1142}


@pytest.mark.asyncio
async def test_correction_excludes_the_entire_previous_variant_group(variant_discovery):
    result = await variant_discovery.service.search(
        "Correctable Song",
        excluded_song_ids=(1161, 1162, 1163),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].song.id == 1164
    assert result.candidates[0].songs == (result.candidates[0].song,)


@pytest.mark.asyncio
async def test_exact_alias_to_worlds_end_keeps_that_record_as_representative(
    variant_discovery,
):
    result = await variant_discovery.service.search(
        "alias points to this WE chart", guild_id=33
    )

    assert result.exact_alias
    assert not result.needs_choice
    candidate = result.candidates[0]
    assert candidate.method == "alias"
    assert candidate.song.id == 1152
    assert {variant.id for variant in candidate.songs} == {1151, 1152}
