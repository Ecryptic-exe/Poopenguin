import json
from contextlib import closing
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chuni_penguin.database import Alias, Chart, Cookie, Song
from chuni_penguin.database.base import Base
from chuni_penguin.providers.chunilib_public import ChunilibPublicProvider
from chuni_penguin.providers.registry import ProviderError
from dbutils.chunilib import (
    backup_sqlite,
    read_catalog_files,
    sync_catalog,
    update_chunilib,
)


def catalog_files(path, *, game_version="mate", extra=False):
    index = [
        {"id": "45", "title": "L9", "artist": "paraoka", "mas": [1017, 58, 0, 24, 4]}
    ]
    constants = {"L9": {"MAS": 14.1}}
    if extra:
        index += [
            {"id": "46", "title": "New fixture", "mas": [10, 0, 0, 0, 0]},
            {"id": "47", "title": "Unknown", "mas": [1]},
        ]
        constants.update({"New fixture": {"MAS": 15.3}, "Unknown": {"MAS": 12.5}})
    path.mkdir(exist_ok=True)
    for name, data in {
        "idx.json": index,
        f"{game_version}.json": constants,
        "version.json": {"version": "20261022v3"},
    }.items():
        (path / name).write_text(json.dumps(data), encoding="utf-8")
    return read_catalog_files(path, game_version)


@pytest_asyncio.fixture
async def catalog(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + (tmp_path / "catalog.sqlite3").as_posix()
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine)() as session, session.begin():
        session.add(
            Song(
                id=45,
                title="L9",
                artist="paraoka",
                genre="VARIETY",
                chunithm_catcode=6,
                version="CHUNITHM",
                available=True,
                removed=False,
            )
        )
        session.add(
            Chart(
                id=313,
                song_id=45,
                difficulty="MAS",
                level="13+",
                const=13.6,
                maxcombo=1103,
                charter="Preserved",
                available=True,
            )
        )
        session.add(
            Alias(alias="fixture alias", song_id=45, guild_id=0, owner_id=None, uses=2)
        )
        session.add(Cookie(discord_id=11, cookie="synthetic fixture"))
    seed = tmp_path / "songs.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "id": 46,
                    "title": "New fixture",
                    "artist": "Fixture",
                    "genre": "ORIGINAL",
                    "chunithm_catcode": 0,
                    "version": "Mate",
                    "available": True,
                    "removed": False,
                    "charts": [
                        {
                            "difficulty": "MAS",
                            "level": "15",
                            "const": 15.0,
                            "available": True,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    try:
        yield engine, seed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_is_read_only_and_apply_preserves_ids_and_player_data(
    catalog, tmp_path
):
    engine, seed = catalog
    snapshot = catalog_files(tmp_path / "source", extra=True)
    before = seed.read_bytes()
    preview = await sync_catalog(engine, snapshot, seed_path=seed)
    assert preview["updated_charts"] == 2 and preview["added_songs"] == 1
    async with async_sessionmaker(engine)() as session:
        assert (await session.get(Chart, 313)).const == 13.6
        assert await session.get(Song, 46) is None
    applied = await sync_catalog(engine, snapshot, apply=True, seed_path=seed)
    assert applied["applied"] and applied["skipped"][0]["song_id"] == 47
    async with async_sessionmaker(engine)() as session:
        row = await session.get(Chart, 313)
        assert (
            row.id,
            row.song_id,
            row.level,
            row.const,
            row.maxcombo,
            row.charter,
        ) == (313, 45, "14", 14.1, 1103, "Preserved")
        assert (await session.scalars(select(Alias))).one().song_id == 45
        assert (await session.get(Cookie, 11)).cookie == "synthetic fixture"
        assert (await session.get(Song, 46)).version == "Mate"
    assert seed.read_bytes() == before
    again = await sync_catalog(engine, snapshot, apply=True, seed_path=seed)
    assert again["updated_charts"] == again["added_charts"] == again["added_songs"] == 0


@pytest.mark.asyncio
async def test_id_title_conflict_is_not_guessed(catalog, tmp_path):
    engine, seed = catalog
    snapshot = catalog_files(tmp_path / "source")
    async with async_sessionmaker(engine)() as session, session.begin():
        song = await session.get(Song, 45)
        song.title = "A different identity"
    result = await sync_catalog(engine, snapshot, apply=True, seed_path=seed)
    assert result["updated_charts"] == 0
    assert result["skipped"][0]["reason"] == "title_mismatch"


@pytest.mark.asyncio
async def test_database_failure_rolls_back_the_entire_update(catalog, tmp_path):
    engine, seed = catalog
    snapshot = catalog_files(tmp_path / "source", extra=True)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TRIGGER reject_new BEFORE INSERT ON chunirec_songs WHEN NEW.id = 46 BEGIN SELECT RAISE(ABORT, 'fixture refusal'); END"
            )
        )
    with pytest.raises(IntegrityError):
        await sync_catalog(engine, snapshot, apply=True, seed_path=seed)
    async with async_sessionmaker(engine)() as session:
        assert (await session.get(Chart, 313)).const == 13.6
        assert await session.get(Song, 46) is None


@pytest.mark.asyncio
async def test_backup_contains_consistent_committed_state(catalog):
    import sqlite3

    engine, _ = catalog
    backup = backup_sqlite(engine)
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT const FROM chunirec_charts WHERE id=313"
        ).fetchone() == (13.6,)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["mate", "crossversex"])
async def test_public_provider_requests_the_selected_version(version):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert (
            "cookie" not in request.headers and "authorization" not in request.headers
        )
        if request.url.path.endswith("version.json"):
            data = {"version": "fixture"}
        elif request.url.path.endswith("idx.json"):
            data = [{"id": "45", "title": "L9", "mas": [1]}]
        else:
            assert request.url.path.endswith(f"/{version}.json")
            data = {"L9": {"MAS": 13.6}}
        return httpx.Response(200, json=data)

    provider = ChunilibPublicProvider(version, transport=httpx.MockTransport(respond))
    try:
        await provider.start()
        snapshot = await provider.read_catalog()
        assert snapshot.provenance.scope.game_version == version
        assert snapshot.provenance.scope.region == "international"
        assert snapshot.songs[0].charts[0].constant == Decimal("13.6")
        await provider.read_catalog()
        assert len(calls) == 4
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_public_request_failure_never_opens_a_backup_or_transaction(monkeypatch):
    async def fail(self):
        raise ProviderError("chunilib_public", "http_403")

    monkeypatch.setattr(ChunilibPublicProvider, "read_catalog", fail)
    backup = Mock(
        side_effect=AssertionError("No database work before a successful fetch")
    )
    monkeypatch.setattr("dbutils.chunilib.backup_sqlite", backup)
    assert not await update_chunilib(
        Mock(), SimpleNamespace(), game_version="mate", apply=True
    )
    backup.assert_not_called()
