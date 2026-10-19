# ruff: noqa: RUF001
import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import PlayerSource
from chuni_penguin.services import review_background
from chuni_penguin.services.chart_reviews import (
    EXPERIENCE_BUCKETS,
    PRIMARY_LEVELS,
    RATING_BUCKETS,
    SKILLS,
)
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.services.review_background import (
    _rating_bucket,
    load_review_background,
)
from chuni_penguin.types import RatingSystem, RatingType

OWNER_ID = 812345678901234567
OTHER_OWNER_ID = 923456789012345678


@pytest_asyncio.fixture
async def database(tmp_path):
    url = await create_fixture_database(tmp_path / "review-background.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        yield database
    finally:
        await database.close()


def make_context(*, database=None, accounts=None, user_id=OWNER_ID):
    services = SimpleNamespace(accounts=accounts, database=database)
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        bot=SimpleNamespace(services=services),
    )


def saved_profile(**changes):
    return {
        "rating_bucket": RATING_BUCKETS[4],
        "primary_level": PRIMARY_LEVELS[5],
        "experience_bucket": EXPERIENCE_BUCKETS[2],
        "strengths": [SKILLS[0], SKILLS[1]],
        "weaknesses": [SKILLS[2]],
        **changes,
    }


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        (0, "未定級"),
        (Decimal("0.01"), "未滿 12"),
        (11.999, "未滿 12"),
        (12, "12–13.99"),
        (Decimal("13.999"), "12–13.99"),
        (14, "14–14.99"),
        (14.999, "14–14.99"),
        (15, "15–15.49"),
        (Decimal("15.499"), "15–15.49"),
        (15.5, "15.5–15.99"),
        (Decimal("15.999"), "15.5–15.99"),
        (16, "16–16.49"),
        (16.5, "16.5–16.99"),
        (Decimal("16.999"), "16.5–16.99"),
        (17, "17 以上"),
        (Decimal("29.999"), "17 以上"),
    ],
)
def test_actual_rating_boundaries(rating, expected):
    assert _rating_bucket(rating) == expected


@pytest.mark.parametrize(
    "rating",
    [True, False, -0.01, 30, float("nan"), float("inf"), Decimal("-Infinity"), "15"],
)
def test_invalid_rating_is_not_bucketed(rating):
    assert _rating_bucket(rating) is None


@pytest.mark.asyncio
async def test_missing_or_invalid_saved_fields_default_to_unavailable():
    expected = {
        "rating_bucket": "未提供",
        "primary_level": "未提供",
        "experience_bucket": "未提供",
        "strengths": [],
        "weaknesses": [],
    }
    assert (
        await load_review_background(make_context(), saved=None, source="other")
        == expected
    )
    assert await load_review_background(
        make_context(),
        saved={
            "rating_bucket": "30.0",
            "primary_level": "99",
            "experience_bucket": [],
            "strengths": ["體力", "unknown", "體力", None],
            "weaknesses": "讀譜",
            "account_id": "private-id",
            "credential": "private-secret",
        },
        source="other",
    ) == {
        **expected,
        "strengths": ["體力"],
    }


@pytest.mark.asyncio
async def test_native_rating_prefers_in_game_and_keeps_other_fields_from_saved():
    client = SimpleNamespace(
        get_minimal_profile=AsyncMock(
            return_value=SimpleNamespace(
                rating_systems=[
                    RatingSystem(type=RatingType.naive, value=20.0),
                    RatingSystem(type=RatingType.in_game, value=Decimal("14.75")),
                ],
                level=99,
                total_credits=9999,
                total_scores=99999,
            )
        ),
        get_profile=AsyncMock(),
    )
    calls = []
    exits = []

    @asynccontextmanager
    async def network(ctx, user_id, **options):
        calls.append((ctx, user_id, options))
        try:
            yield client
        finally:
            exits.append(True)

    saved = saved_profile()
    result = await load_review_background(
        make_context(accounts=SimpleNamespace(network=network)),
        saved=saved,
        source="net",
    )

    assert result == {**saved, "rating_bucket": "14–14.99"}
    assert calls[0][1:] == (
        OWNER_ID,
        {"kamaitachi": False, "chunithm_net": True},
    )
    assert client.get_profile.await_count == 0
    assert exits == [True]
    assert "level" not in result and "total_credits" not in result


@pytest.mark.asyncio
async def test_minimal_without_in_game_rating_falls_back_to_full_profile():
    client = SimpleNamespace(
        get_minimal_profile=AsyncMock(
            return_value=SimpleNamespace(
                rating_systems=[RatingSystem(type=RatingType.naive, value=25.0)]
            )
        ),
        get_profile=AsyncMock(
            return_value=SimpleNamespace(
                rating_systems=[RatingSystem(type=RatingType.in_game, value=16.5)]
            )
        ),
    )

    @asynccontextmanager
    async def network(ctx, user_id, **options):
        assert options == {"kamaitachi": True, "chunithm_net": False}
        yield client

    result = await load_review_background(
        make_context(accounts=SimpleNamespace(network=network)),
        source="kamaitachi",
    )

    assert result["rating_bucket"] == "16.5–16.99"
    assert result["primary_level"] == "未提供"
    assert result["experience_bucket"] == "未提供"
    assert result["strengths"] == result["weaknesses"] == []
    client.get_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_network_source_uses_account_auto_selection():
    client = SimpleNamespace(
        get_minimal_profile=AsyncMock(
            return_value=SimpleNamespace(
                rating_systems=[RatingSystem(type=RatingType.in_game, value=15.5)]
            )
        ),
        get_profile=AsyncMock(),
    )
    options_seen = []

    @asynccontextmanager
    async def network(ctx, user_id, **options):
        options_seen.append(options)
        yield client

    await load_review_background(
        make_context(accounts=SimpleNamespace(network=network)), source="default"
    )

    assert options_seen == [{"kamaitachi": False, "chunithm_net": False}]


@pytest.mark.asyncio
async def test_native_failure_falls_back_without_exposing_exception_payload():
    secret = "private-auth-payload"
    client = SimpleNamespace(
        get_minimal_profile=AsyncMock(side_effect=RuntimeError(secret)),
        get_profile=AsyncMock(),
    )
    exited = []

    @asynccontextmanager
    async def network(ctx, user_id, **options):
        try:
            yield client
        finally:
            exited.append(True)

    saved = saved_profile()
    result = await load_review_background(
        make_context(accounts=SimpleNamespace(network=network)), saved=saved
    )

    assert result == saved
    assert secret not in repr(result)
    assert exited == [True]


@pytest.mark.asyncio
async def test_native_timeout_falls_back_and_closes_context(monkeypatch):
    original_timeout = asyncio.timeout
    started = asyncio.Event()
    exited = []

    @asynccontextmanager
    async def network(ctx, user_id, **options):
        try:
            yield SimpleNamespace(
                get_minimal_profile=wait_for_profile,
                get_profile=AsyncMock(),
            )
        finally:
            exited.append(True)

    async def wait_for_profile():
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        review_background.asyncio, "timeout", lambda seconds: original_timeout(0.01)
    )
    result = await load_review_background(
        make_context(accounts=SimpleNamespace(network=network)),
        saved=saved_profile(),
    )

    assert started.is_set()
    assert result == saved_profile()
    assert exited == [True]


@pytest.mark.asyncio
async def test_external_cancellation_propagates_after_network_cleanup():
    started = asyncio.Event()
    exited = []

    async def wait_for_profile():
        started.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def network(ctx, user_id, **options):
        try:
            yield SimpleNamespace(
                get_minimal_profile=wait_for_profile,
                get_profile=AsyncMock(),
            )
        finally:
            exited.append(True)

    task = asyncio.create_task(
        load_review_background(
            make_context(accounts=SimpleNamespace(network=network)),
            saved=saved_profile(),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert exited == [True]


@pytest.mark.asyncio
async def test_explicit_sources_read_only_owners_rating_and_exclude_secrets(database):
    for owner_id, source, rating, credential in (
        (OWNER_ID, "chunirec:jp", 12, "owner-token"),
        (OWNER_ID, "chunirec:jp2", 15.5, "other-owner-token"),
        (OWNER_ID, "chunilib:export", 17, "export-secret"),
        (OTHER_OWNER_ID, "chunirec:jp", 20, "other-user-secret"),
    ):
        await database.writer.merge(
            PlayerSource(
                discord_id=owner_id,
                source=source,
                credential=credential,
                payload=json.dumps(
                    {
                        "rating": rating,
                        "account_id": "source-account-id",
                        "records": [{"name": "private-song-data"}],
                    }
                ),
                status="synced",
            )
        )

    expected = {
        "chunirec:jp": "12–13.99",
        "chunirec:jp2": "15.5–15.99",
        "chunilib:export": "17 以上",
    }
    for source, rating_bucket in expected.items():
        result = await load_review_background(
            make_context(database=database, user_id=OWNER_ID),
            saved=saved_profile(),
            source=source,
        )
        assert result == {**saved_profile(), "rating_bucket": rating_bucket}

    other_result = await load_review_background(
        make_context(database=database, user_id=OTHER_OWNER_ID),
        source="chunirec:jp",
    )
    assert other_result["rating_bucket"] == "17 以上"
    rendered = repr((result, other_result))
    for secret in (
        "owner-token",
        "other-owner-token",
        "export-secret",
        "other-user-secret",
        "source-account-id",
        "private-song-data",
        str(OWNER_ID),
        str(OTHER_OWNER_ID),
    ):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_missing_or_invalid_snapshot_rating_keeps_saved_rating(database):
    for source, payload in (
        ("chunirec:jp", json.dumps({"rating": "14.5"})),
        ("chunirec:jp2", json.dumps({"rating": True})),
        ("chunilib:export", "not-json"),
    ):
        await database.writer.merge(
            PlayerSource(
                discord_id=OWNER_ID,
                source=source,
                credential="must-not-be-read",
                payload=payload,
                status="synced",
            )
        )
    saved = saved_profile()
    for source in ("chunirec:jp", "chunirec:jp2", "chunilib:export"):
        assert (
            await load_review_background(
                make_context(database=database), saved=saved, source=source
            )
            == saved
        )
