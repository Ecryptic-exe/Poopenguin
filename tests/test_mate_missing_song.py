"""Reproduce an absent Mate song failing PB writes, then exercise the catalog fix."""

import io
from dataclasses import replace

import pytest
from discord.utils import MISSING
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from chuni_penguin.adapters.utils import process_records
from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import PersonalBest as StoredPersonalBest
from chuni_penguin.renderers.b50 import render_b30
from chuni_penguin.services.catalog import AliasService, SongSearchService
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.types import (
    Chart,
    ClearLamp,
    ComboLamp,
    Difficulty,
    PersonalBest,
    Song,
)
from dbutils.chunilib import DEFAULT_CATALOG_DIR, read_catalog_files, sync_catalog


def record(song_id):
    return PersonalBest(
        song=Song(
            id=song_id,
            title="狂命",
            jacket_url="https://chunithm-net-eng.com/mobile/img/5f167154120c68ed.jpg",
        ),
        chart=Chart(difficulty=Difficulty.master),
        score=1_009_000,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.full_combo,
    )


@pytest.mark.asyncio
async def test_missing_kyoumei_breaks_pb_write_then_seed_import_fixes_it(tmp_path):
    url = await create_fixture_database(tmp_path / "mate.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    engine = create_async_engine(url)
    await database.start()
    try:
        # Rating-list records carry the native ID. The catalog FK must exist.
        with pytest.raises(IntegrityError) as error:
            await process_records(
                database, 11, "CHUNITHM International", [record(3069)]
            )
        assert "FOREIGN KEY constraint failed" in str(error.value.orig)

        snapshot = read_catalog_files(DEFAULT_CATALOG_DIR, "mate")
        snapshot = replace(
            snapshot,
            songs=tuple(song for song in snapshot.songs if song.ref.id == "3069"),
        )
        preview = await sync_catalog(engine, snapshot)
        assert (preview["added_songs"], preview["added_charts"]) == (1, 4)
        assert not preview["skipped"]
        applied = await sync_catalog(engine, snapshot, apply=True)
        assert (applied["added_songs"], applied["added_charts"]) == (1, 4)

        # Both native-ID rating records and jacket-only recent records hydrate.
        for song_id in (3069, MISSING):
            hydrated = (
                await process_records(
                    database, 11, "CHUNITHM International", [record(song_id)]
                )
            )[0]
            assert hydrated.song.id == 3069 and hydrated.song.version == "Mate"
            assert hydrated.chart.internal_level == 15.2
            assert hydrated.chart.max_combo == 2451
            assert hydrated.rating is not None and hydrated.overpower is not None
        async with database.read_sessionmaker() as session:
            stored = (await session.scalars(select(StoredPersonalBest))).one()
            assert (stored.song_id, stored.difficulty, stored.score) == (
                3069, "MAS", 1_009_000
            )

        aliases = AliasService(database)
        await aliases.reload()
        song, alias, similarity = await SongSearchService(database, aliases).find_song(
            "狂命"
        )
        assert song.id == 3069 and alias is None and similarity == 100
        output = io.BytesIO()
        render_b30(
            player_name="Mate fixture",
            output=output,
            records=[],
            record_slots=30,
            new_records=[hydrated],
            new_record_slots=20,
        )
        assert output.getvalue().startswith(b"\x89PNG\r\n\x1a\n")

        again = await sync_catalog(engine, snapshot)
        assert (again["added_songs"], again["added_charts"], again["updated_charts"]) == (
            0, 0, 0
        )
    finally:
        await database.close()
        await engine.dispose()
