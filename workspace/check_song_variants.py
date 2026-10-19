"""Verify discovery grouping against the live public catalog without writes."""

import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chuni_penguin.database import Song
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.song_discovery import SongDiscoveryService, search_key

ROOT = Path(__file__).resolve().parents[1]


async def main():
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=(ROOT / "data/penguin.sqlite3").as_uri(),
            query={"mode": "ro", "uri": "true"},
        ),
        hide_parameters=True,
    )
    try:
        maker = async_sessionmaker(engine)
        database = SimpleNamespace(
            read_sessionmaker=maker, mapping_read_sessionmaker=maker
        )
        async with maker() as session:
            records = list(await session.scalars(select(Song)))
        titles = defaultdict(list)
        for song in records:
            titles[song.title].append(song)
        duplicates = {title: rows for title, rows in titles.items() if len(rows) > 1}
        aliases = AliasService(database)
        await aliases.reload()
        service = SongDiscoveryService(database, aliases)
        largest_menu = 0
        samples = []
        for title, rows in duplicates.items():
            exact_alias_ids = {
                entry.song_id
                for entry in aliases.alias_cache.get(0, [])
                if entry.id is not None and search_key(entry.alias) == search_key(title)
            }
            visible_families = {
                (search_key(song.title), search_key(song.artist))
                for song in records
                if search_key(song.title) == search_key(title)
                or song.id in exact_alias_ids
            }
            rows = [
                song
                for song in records
                if (search_key(song.title), search_key(song.artist)) in visible_families
            ]
            families = defaultdict(list)
            for song in rows:
                families[(search_key(song.title), search_key(song.artist))].append(song)
            expected = sum(
                1
                if sum(song.genre != "WORLD'S END" for song in family) <= 1
                else len(family)
                for family in families.values()
            )
            found = await service.search(title)
            assert len(found.candidates) == expected, title
            assert found.needs_choice == (expected > 1), title
            assert Counter(
                song.id for candidate in found.candidates for song in candidate.songs
            ) == Counter(song.id for song in rows), title
            for candidate in found.candidates:
                ordinary = [
                    song for song in candidate.songs if song.genre != "WORLD'S END"
                ]
                if ordinary:
                    assert candidate.song.id == ordinary[0].id, title
                count = sum(len(song.charts) for song in candidate.songs)
                largest_menu = max(largest_menu, count)
                assert count <= 25, title
            if title in (
                "G e n g a o z o",
                "Invitation",
                "volcanic",
                "Random",
                "The ether",
                "裏表ラバーズ",
            ):
                samples.append(
                    {
                        "title": title,
                        "before_rows": len(rows),
                        "song_candidates": len(found.candidates),
                        "default_song_id": found.candidates[0].song.id,
                        "all_record_ids": [
                            song.id
                            for candidate in found.candidates
                            for song in candidate.songs
                        ],
                    }
                )
        reported = await service.search("gengaozo")
        assert len(reported.candidates) == 1 and not reported.needs_choice
        assert reported.candidates[0].song.id == 506
        assert {song.id for song in reported.candidates[0].songs} == {
            506,
            8108,
            8137,
            8203,
        }
        result = {
            "complete": True,
            "duplicate_titles_checked": len(duplicates),
            "largest_difficulty_menu": largest_menu,
            "all_canonical_ids_retained": True,
            "default_normal_record": True,
            "readonly_uri": True,
            "reported_query": "gengaozo",
            "reported_query_candidates": len(reported.candidates),
            "reported_query_default": reported.candidates[0].song.id,
            "reported_query_variants": len(reported.candidates[0].variants),
            "samples": samples,
        }
        (ROOT / "reports/song-variant-catalog-20261016.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
