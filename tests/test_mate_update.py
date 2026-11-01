import io
import os
import shutil
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import ImageChops

from chuni_penguin.adapters.kamaitachi import KamaitachiAdapter
from chuni_penguin.adapters.utils import calculate_ongeki_rating_breakdown
from chuni_penguin.cogs.chunithm.best_plays import BestPlaysCog
from chuni_penguin.converters import VersionConverter
from chuni_penguin.renderers import b50
from chuni_penguin.types import (
    Chart,
    ClearLamp,
    ComboLamp,
    Difficulty,
    PersonalBest,
    RatingFrameType,
    RatingType,
    Song,
)


def record(song_id, version):
    return PersonalBest(
        song=Song(id=song_id, title="Version fixture", jacket_url="", version=version),
        chart=Chart(difficulty=Difficulty.master, level="14", internal_level=14.0),
        score=1_009_000,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.all_justice,
        rating=Decimal("16"),
        ongeki_rating=Decimal("16"),
        ongeki_platinum_rating=Decimal("0.1"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["Mate", "mate", "MATE"])
async def test_mate_version_is_case_insensitive(value):
    assert await VersionConverter().convert(None, value) == "Mate"


@pytest.mark.asyncio
async def test_kamaitachi_keeps_its_own_new_chart_version(monkeypatch):
    adapter = KamaitachiAdapter.__new__(KamaitachiAdapter)
    monkeypatch.setattr(KamaitachiAdapter, "get_all_personal_bests", AsyncMock(
        return_value=[record(1, "Mate"), record(2, "X-VERSE-X")]
    ))
    result = await adapter.get_rating_breakdown(RatingType.in_game)
    assert [row.song.id for row in result.frames[RatingFrameType.new].scores] == [2]
    assert [row.song.id for row in result.frames[RatingFrameType.best].scores] == [1]


@pytest.mark.parametrize("version", ["Mate", "X-VERSE-X"])
def test_ongeki_new_frame_uses_the_requested_source_version(version):
    result = calculate_ongeki_rating_breakdown(
        RatingType.ongeki, [record(1, "Mate"), record(2, "X-VERSE-X")], version
    )
    assert all(
        row.song.version == version for row in result.frames[RatingFrameType.new].scores
    )
    assert len(result.frames[RatingFrameType.new].scores) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kamaitachi, expected", [(False, "default"), (True, "xversex")]
)
async def test_best50_routes_the_theme_by_source(kamaitachi, expected, monkeypatch):
    client = (
        KamaitachiAdapter.__new__(KamaitachiAdapter)
        if kamaitachi
        else SimpleNamespace()
    )
    profile = AsyncMock(return_value=SimpleNamespace(username="Fixture"))
    breakdown = AsyncMock(
        return_value=SimpleNamespace(
            rating=Decimal("0"),
            frames={RatingFrameType.best: SimpleNamespace(scores=[], num_scores=30)},
        )
    )

    if kamaitachi:
        monkeypatch.setattr(KamaitachiAdapter, "get_profile", profile)
        monkeypatch.setattr(KamaitachiAdapter, "get_rating_breakdown", breakdown)
    else:
        client.get_profile = profile
        client.get_rating_breakdown = breakdown

    @asynccontextmanager
    async def network(*args, **kwargs):
        yield client

    @asynccontextmanager
    async def typing():
        yield

    cog = BestPlaysCog.__new__(BestPlaysCog)
    cog.accounts = SimpleNamespace(network=network)
    cog.users = SimpleNamespace(fetch_user_config=AsyncMock(return_value=None))
    cog.database = SimpleNamespace(
        cookies=SimpleNamespace(
            get_by_discord_id=AsyncMock(return_value=SimpleNamespace(cookie="fixture"))
        )
    )
    cog._best50_respond = AsyncMock()
    ctx = SimpleNamespace(author=SimpleNamespace(id=11), typing=typing)
    await cog._best50_inner(ctx, kamaitachi=kamaitachi, rating_system="ingame")
    assert cog._best50_respond.await_args.args[-1] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("theme, expected", [("default", False), ("xversex", True)])
async def test_mate_never_draws_a_removed_uncross_logo(monkeypatch, theme, expected):
    rendered = []

    def render(**kwargs):
        rendered.append(kwargs)
        kwargs["output"].write(b"fixture")

    monkeypatch.setattr("chuni_penguin.cogs.chunithm.best_plays.render_b30", render)
    cog = BestPlaysCog.__new__(BestPlaysCog)
    cog._random = SimpleNamespace(random=lambda: 0)
    cog.database = SimpleNamespace(user_found_easter_egg=AsyncMock())
    cog._prepare_jackets = AsyncMock()
    async def respond(*, files):
        for attachment in files:
            attachment.close()

    ctx = SimpleNamespace(author=SimpleNamespace(id=11), respond_or_edit=respond)
    await cog._best50_respond(
        ctx,
        SimpleNamespace(username="Fixture"),
        None,
        0,
        [],
        30,
        [],
        20,
        theme,
        classic=False,
    )
    assert rendered[0]["uncross_verse"] is expected
    assert bool(cog.database.user_found_easter_egg.await_count) is expected


def test_theme_cache_separates_zip_assets_with_equal_timestamps(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    (cache / "b50").mkdir(parents=True)
    monkeypatch.setattr(b50, "CACHE_DIR", cache)
    paths = []
    for theme in ("default", "xversex"):
        folder = tmp_path / theme
        folder.mkdir()
        for name in ("bg.webp", "logo.webp"):
            path = folder / name
            shutil.copyfile(b50.ASSETS_DIR / "b50" / theme / name, path)
            os.utime(path, (1700000000, 1700000000))
        paths.append(folder)
    with b50._render_b30_template(
        5, bg_path=paths[0] / "bg.webp", logo_path=paths[0] / "logo.webp"
    ) as mate:
        with b50._render_b30_template(
            5, bg_path=paths[1] / "bg.webp", logo_path=paths[1] / "logo.webp"
        ) as xversex:
            assert ImageChops.difference(
                mate.convert("RGB"), xversex.convert("RGB")
            ).getbbox()
    assert len(list((cache / "b50").glob("template_*.webp"))) == 2


def test_default_renderer_ignores_uncross_and_generates_mate_without_that_asset():
    output = io.BytesIO()
    b50.render_b30(
        player_name="Mate fixture",
        output=output,
        records=[record(1, "Mate")],
        record_slots=5,
        uncross_verse=True,
    )
    assert output.getvalue().startswith(b"\x89PNG\r\n\x1a\n")
