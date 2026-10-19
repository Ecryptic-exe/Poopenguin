import io
import tempfile
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

from chuni_penguin.cogs.chunithm.best_plays import BestPlaysCog
from chuni_penguin.cogs.v2_players import PlayersCog
from chuni_penguin.context import PenguinContext
from chuni_penguin.database import UserConfig
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
from chuni_penguin.utils import AsyncTemporaryFile


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["native", "named"])
async def test_native_b50_renders_and_delivers_real_discord_attachment(
    monkeypatch, backend
):
    if backend == "named":
        # Windows TemporaryFile is a NamedTemporaryFile wrapper. Force the same
        # behavior on Unix so Linux CI also covers the actual production failure.
        monkeypatch.setattr(tempfile, "TemporaryFile", tempfile.NamedTemporaryFile)

    row = PersonalBest(
        song=Song(id=1, title="Attachment fixture", jacket_url=""),
        chart=Chart(difficulty=Difficulty.master, level="14+", internal_level=14.7),
        score=1007500,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.all_justice,
        rating=Decimal("16.70"),
    )
    client = SimpleNamespace(
        DEFAULT_RATING_SYSTEM=RatingType.in_game,
        get_profile=AsyncMock(
            return_value=SimpleNamespace(username="Attachment fixture")
        ),
        get_rating_breakdown=AsyncMock(
            return_value=SimpleNamespace(
                rating=Decimal("16.70"),
                frames={
                    RatingFrameType.best: SimpleNamespace(scores=[row], num_scores=30),
                    RatingFrameType.new: SimpleNamespace(scores=[], num_scores=20),
                },
            )
        ),
    )

    @asynccontextmanager
    async def network(ctx, target, *, kamaitachi):
        assert target == 11 and kamaitachi is False
        yield client

    @asynccontextmanager
    async def typing():
        yield

    delivered = []

    async def respond(*, files):
        (attachment,) = files
        assert isinstance(attachment, discord.File)
        assert isinstance(attachment.fp, io.IOBase)
        assert attachment.filename.endswith(".png")
        assert attachment.fp.tell() == 0
        with Image.open(io.BytesIO(attachment.fp.read())) as image:
            assert image.format == "PNG" and image.width == 1872 and image.height > 2000
        # The real Discord transport resets uploads when retrying and closes its
        # File wrapper; the temporary-file owner must still close the stream.
        attachment.reset(seek=True)
        assert attachment.fp.read(8) == b"\x89PNG\r\n\x1a\n"
        delivered.append(attachment.fp)
        attachment.close()

    config = UserConfig(discord_id=11)
    best = BestPlaysCog.__new__(BestPlaysCog)
    best.accounts = SimpleNamespace(network=network)
    best.users = SimpleNamespace(fetch_user_config=AsyncMock(return_value=config))
    best.database = SimpleNamespace(
        cookies=SimpleNamespace(
            get_by_discord_id=AsyncMock(
                return_value=SimpleNamespace(cookie="#LWP-Cookies-2.0 fixture")
            ),
        )
    )
    best._random = SimpleNamespace(random=lambda: 1)
    best.jacket_cache = SimpleNamespace(ensure=AsyncMock())
    cog = PlayersCog.__new__(PlayersCog)
    cog.best = best
    cog.bot = SimpleNamespace(database=best.database)
    ctx = SimpleNamespace(
        author=SimpleNamespace(id=11),
        user_config=config,
        typing=typing,
        respond_or_edit=respond,
    )
    monkeypatch.setattr(PenguinContext, "from_interaction", AsyncMock(return_value=ctx))
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=11), response=SimpleNamespace(defer=AsyncMock())
    )
    await PlayersCog.b50.callback(cog, interaction, source="net")
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert len(delivered) == 1 and delivered[0].closed


@pytest.mark.asyncio
async def test_named_temporary_file_owner_removes_file_on_delivery_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(tempfile, "TemporaryFile", tempfile.NamedTemporaryFile)
    with pytest.raises(RuntimeError, match="simulated send failure"):
        async with AsyncTemporaryFile(dir=str(tmp_path)) as stream:
            assert isinstance(stream, io.IOBase)
            stream.write(b"fixture")
            file = discord.File(stream, filename="fixture.txt")
            file.close()
            raise RuntimeError("simulated send failure")
    assert stream.closed and not list(tmp_path.iterdir())
