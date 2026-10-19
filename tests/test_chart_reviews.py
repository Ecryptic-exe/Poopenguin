import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import (
    Chart,
    ChartPlayerReview,
    ChartPlayerReviewHistory,
    ChartReviewerProfile,
)
from chuni_penguin.services.chart_reviews import (
    EXPERIENCE_BUCKETS,
    FAMILIARITIES,
    RATING_BUCKETS,
    ChartReviewService,
)
from chuni_penguin.services.database import DatabaseService

UINT64_OWNER = 2**63 + 123_456_789
OTHER_OWNER = 2**63 + 987_654_321


@pytest_asyncio.fixture
async def database(tmp_path):
    url = await create_fixture_database(tmp_path / "chart-reviews.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        yield database
    finally:
        await database.close()


def reviewer_profile(**changes):
    return {
        "rating_bucket": RATING_BUCKETS[4],
        "primary_level": "14",
        "experience_bucket": EXPERIENCE_BUCKETS[2],
        "strengths": ["體力", "讀譜"],
        "weaknesses": ["鍵盤"],
        **changes,
    }


def chart_review(**changes):
    return {
        "enjoyment": 4,
        "perceived_difficulty": 0,
        "familiarity": FAMILIARITIES[2],
        "goal": "SSS",
        "comment": "這是只供本人查看的測試評論。",
        **changes,
    }


async def add_chart(database, chart_id):
    await database.writer.add(
        Chart(
            id=chart_id,
            song_id=900000001,
            difficulty=f"MAS-{chart_id}",
            level="14",
            const=14.0,
            available=True,
        )
    )
    return chart_id


async def table_counts(database):
    async with database.mapping_read_sessionmaker() as session:
        return {
            model.__tablename__: await session.scalar(
                select(func.count()).select_from(model)
            )
            for model in (
                ChartReviewerProfile,
                ChartPlayerReview,
                ChartPlayerReviewHistory,
            )
        }


async def history_for(database, user_id, chart_id):
    async with database.mapping_read_sessionmaker() as session:
        return list(
            await session.execute(
                select(
                    ChartPlayerReviewHistory.revision,
                    ChartPlayerReviewHistory.review_json,
                )
                .join(
                    ChartPlayerReview,
                    ChartPlayerReviewHistory.review_id == ChartPlayerReview.id,
                )
                .where(
                    ChartPlayerReview.discord_id == user_id,
                    ChartPlayerReview.chart_id == chart_id,
                )
                .order_by(ChartPlayerReviewHistory.revision)
            )
        )


async def review_id_for(database, user_id, chart_id):
    async with database.mapping_read_sessionmaker() as session:
        return await session.scalar(
            select(ChartPlayerReview.id).where(
                ChartPlayerReview.discord_id == user_id,
                ChartPlayerReview.chart_id == chart_id,
            )
        )


async def history_for_review_id(database, review_id):
    async with database.mapping_read_sessionmaker() as session:
        return list(
            await session.scalars(
                select(ChartPlayerReviewHistory).where(
                    ChartPlayerReviewHistory.review_id == review_id
                )
            )
        )


@pytest.mark.asyncio
async def test_submit_upserts_revision_history_and_uint64_owner(database):
    chart_id = await add_chart(database, 700001)
    service = ChartReviewService(database)
    first_profile = reviewer_profile(strengths=["體力"], weaknesses=["鍵盤"])
    first_review = chart_review(
        enjoyment=1, perceived_difficulty=-2, comment="第一次私人評論"
    )
    second_profile = reviewer_profile(
        rating_bucket=RATING_BUCKETS[6],
        primary_level="15",
        experience_bucket=EXPERIENCE_BUCKETS[3],
        strengths=["交互"],
        weaknesses=["滑鍵"],
    )
    second_review = chart_review(
        enjoyment=5,
        perceived_difficulty=2,
        familiarity=FAMILIARITIES[1],
        goal="FC",
        comment="更新後的私人評論",
    )

    assert (
        await service.submit(UINT64_OWNER, chart_id, first_profile, first_review) == 1
    )
    assert (
        await service.submit(
            UINT64_OWNER,
            chart_id,
            second_profile,
            second_review,
            expected_revision=1,
        )
        == 2
    )

    saved = await service.own_review(UINT64_OWNER, chart_id)
    assert saved == {
        **second_review,
        "revision": 2,
        "profile": second_profile,
    }
    assert await service.profile(UINT64_OWNER) == second_profile

    history = await history_for(database, UINT64_OWNER, chart_id)
    assert [row.revision for row in history] == [1, 2]
    assert json.loads(history[0].review_json) == {
        **first_review,
        "profile": first_profile,
    }
    assert json.loads(history[1].review_json) == {
        **second_review,
        "profile": second_profile,
    }

    async with database.mapping_read_sessionmaker() as session:
        stored = await session.scalar(
            select(ChartPlayerReview).where(
                ChartPlayerReview.discord_id == UINT64_OWNER,
                ChartPlayerReview.chart_id == chart_id,
            )
        )
    assert stored is not None and stored.discord_id == UINT64_OWNER


@pytest.mark.asyncio
async def test_profile_strengths_and_weaknesses_are_optional(database):
    chart_id = await add_chart(database, 700016)
    service = ChartReviewService(database)
    profile = reviewer_profile()
    profile.pop("strengths")
    profile.pop("weaknesses")
    expected_profile = {
        **profile,
        "strengths": [],
        "weaknesses": [],
    }

    await service.submit(UINT64_OWNER, chart_id, profile, chart_review())

    assert await service.profile(UINT64_OWNER) == expected_profile
    assert (await service.own_review(UINT64_OWNER, chart_id))["profile"] == (
        expected_profile
    )


@pytest.mark.asyncio
async def test_profile_change_does_not_rewrite_another_charts_snapshot(database):
    first_chart = await add_chart(database, 700002)
    second_chart = await add_chart(database, 700003)
    service = ChartReviewService(database)
    first_profile = reviewer_profile()
    second_profile = reviewer_profile(
        rating_bucket=RATING_BUCKETS[3],
        primary_level="13+",
        experience_bucket=EXPERIENCE_BUCKETS[4],
        strengths=["Air"],
        weaknesses=["讀譜"],
    )

    await service.submit(UINT64_OWNER, first_chart, first_profile, chart_review())
    await service.submit(UINT64_OWNER, second_chart, second_profile, chart_review())

    assert await service.profile(UINT64_OWNER) == second_profile
    assert (await service.own_review(UINT64_OWNER, first_chart))["profile"] == (
        first_profile
    )
    assert (await service.own_review(UINT64_OWNER, second_chart))["profile"] == (
        second_profile
    )
    first_history = await history_for(database, UINT64_OWNER, first_chart)
    assert json.loads(first_history[0].review_json)["profile"] == first_profile


@pytest.mark.asyncio
async def test_stale_revision_rejection_has_no_profile_or_history_side_effects(
    database,
):
    chart_id = await add_chart(database, 700004)
    service = ChartReviewService(database)
    original_profile = reviewer_profile()
    original_review = chart_review(comment="保留中的評論")
    changed_profile = reviewer_profile(
        rating_bucket=RATING_BUCKETS[7], strengths=["交互"], weaknesses=["Air"]
    )

    await service.submit(UINT64_OWNER, chart_id, original_profile, original_review)
    with pytest.raises(ValueError, match="另一個視窗更新"):
        await service.submit(
            UINT64_OWNER,
            chart_id,
            changed_profile,
            chart_review(comment="過期視窗的評論"),
            expected_revision=0,
        )

    assert await service.profile(UINT64_OWNER) == original_profile
    assert await service.own_review(UINT64_OWNER, chart_id) == {
        **original_review,
        "revision": 1,
        "profile": original_profile,
    }
    assert [
        row.revision for row in await history_for(database, UINT64_OWNER, chart_id)
    ] == [1]


@pytest.mark.asyncio
async def test_wrong_chart_and_other_owner_cannot_overwrite_existing_review(database):
    first_chart = await add_chart(database, 700005)
    second_chart = await add_chart(database, 700006)
    service = ChartReviewService(database)
    original_profile = reviewer_profile()
    original_review = chart_review(comment="原作者評論")
    changed_profile = reviewer_profile(
        rating_bucket=RATING_BUCKETS[6], strengths=["交互"], weaknesses=["滑鍵"]
    )

    await service.submit(UINT64_OWNER, first_chart, original_profile, original_review)
    with pytest.raises(ValueError, match="另一個視窗更新"):
        await service.submit(
            UINT64_OWNER,
            second_chart,
            changed_profile,
            chart_review(comment="錯誤譜面評論"),
            expected_revision=1,
        )
    with pytest.raises(ValueError, match="另一個視窗更新"):
        await service.submit(
            OTHER_OWNER,
            first_chart,
            changed_profile,
            chart_review(comment="其他使用者的過期評論"),
            expected_revision=1,
        )

    assert await service.profile(UINT64_OWNER) == original_profile
    assert await service.profile(OTHER_OWNER) is None
    assert await service.own_review(UINT64_OWNER, first_chart) == {
        **original_review,
        "revision": 1,
        "profile": original_profile,
    }
    assert await service.own_review(UINT64_OWNER, second_chart) is None
    assert await service.own_review(OTHER_OWNER, first_chart) is None

    # A separate owner may create their own row for the same chart.
    assert (
        await service.submit(OTHER_OWNER, first_chart, changed_profile, chart_review())
        == 1
    )
    assert await service.own_review(UINT64_OWNER, first_chart) == {
        **original_review,
        "revision": 1,
        "profile": original_profile,
    }


@pytest.mark.asyncio
async def test_chart_foreign_key_is_enforced_by_the_isolated_database(database):
    chart_id = await add_chart(database, 700007)
    service = ChartReviewService(database)
    await service.submit(UINT64_OWNER, chart_id, reviewer_profile(), chart_review())
    now = datetime.now(UTC)
    orphan = ChartPlayerReview(
        chart_id=999999,
        discord_id=UINT64_OWNER,
        enjoyment=4,
        perceived_difficulty=0,
        familiarity=FAMILIARITIES[2],
        goal="SSS",
        comment="FK 測試列",
        profile_snapshot_json=json.dumps(reviewer_profile(), ensure_ascii=False),
        revision=1,
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        await database.writer.add(orphan)

    assert await service.own_review(UINT64_OWNER, 999999) is None
    assert await history_for(database, UINT64_OWNER, chart_id)
    assert await table_counts(database) == {
        "chart_reviewer_profiles": 1,
        "chart_player_reviews": 1,
        "chart_player_review_history": 1,
    }


@pytest.mark.asyncio
async def test_invalid_inputs_leave_profile_review_and_history_tables_empty(database):
    chart_id = await add_chart(database, 700008)
    service = ChartReviewService(database)
    invalid_cases = [
        ({"rating_bucket": None}, {}, {}, UINT64_OWNER, chart_id),
        ({"primary_level": "10"}, {}, {}, UINT64_OWNER, chart_id),
        ({"experience_bucket": "十年"}, {}, {}, UINT64_OWNER, chart_id),
        ({"strengths": "體力"}, {}, {}, UINT64_OWNER, chart_id),
        ({"weaknesses": ["不存在的項目"]}, {}, {}, UINT64_OWNER, chart_id),
        ({}, {"enjoyment": 0}, {}, UINT64_OWNER, chart_id),
        ({}, {"enjoyment": 6}, {}, UINT64_OWNER, chart_id),
        ({}, {"enjoyment": True}, {}, UINT64_OWNER, chart_id),
        ({}, {"perceived_difficulty": -3}, {}, UINT64_OWNER, chart_id),
        ({}, {"perceived_difficulty": 3}, {}, UINT64_OWNER, chart_id),
        ({}, {"familiarity": "很多次"}, {}, UINT64_OWNER, chart_id),
        ({}, {"goal": "理論値以上"}, {}, UINT64_OWNER, chart_id),
        ({}, {"comment": "   "}, {}, UINT64_OWNER, chart_id),
        ({}, {"comment": "x" * 1001}, {}, UINT64_OWNER, chart_id),
        ({}, {}, {"expected_revision": -1}, UINT64_OWNER, chart_id),
        ({}, {}, {"expected_revision": True}, UINT64_OWNER, chart_id),
        ({}, {}, {}, 0, chart_id),
        ({}, {}, {}, True, chart_id),
        ({}, {}, {}, 2**64, chart_id),
        ({}, {}, {}, UINT64_OWNER, 0),
        ({}, {}, {}, UINT64_OWNER, True),
        ({}, {}, {}, UINT64_OWNER, 999999),
    ]

    for (
        profile_changes,
        review_changes,
        submit_kwargs,
        user_id,
        target_chart,
    ) in invalid_cases:
        with pytest.raises(ValueError):
            await service.submit(
                user_id,
                target_chart,
                reviewer_profile(**profile_changes),
                chart_review(**review_changes),
                **submit_kwargs,
            )

    assert await service.profile(UINT64_OWNER) is None
    assert await service.own_review(UINT64_OWNER, chart_id) is None
    assert await table_counts(database) == {
        "chart_reviewer_profiles": 0,
        "chart_player_reviews": 0,
        "chart_player_review_history": 0,
    }


@pytest.mark.asyncio
async def test_delete_removes_only_owners_review_and_its_history(database):
    first_chart = await add_chart(database, 700009)
    second_chart = await add_chart(database, 700010)
    service = ChartReviewService(database)
    await service.submit(UINT64_OWNER, first_chart, reviewer_profile(), chart_review())
    await service.submit(UINT64_OWNER, second_chart, reviewer_profile(), chart_review())
    await service.submit(OTHER_OWNER, first_chart, reviewer_profile(), chart_review())
    owner_review_id = await review_id_for(database, UINT64_OWNER, first_chart)
    other_review_id = await review_id_for(database, OTHER_OWNER, first_chart)
    second_review_id = await review_id_for(database, UINT64_OWNER, second_chart)

    assert await service.delete(UINT64_OWNER, first_chart) is True
    assert await service.delete(UINT64_OWNER, first_chart) is False
    assert await service.profile(UINT64_OWNER) == reviewer_profile()
    assert await service.own_review(UINT64_OWNER, first_chart) is None
    assert await service.own_review(OTHER_OWNER, first_chart) is not None
    assert await service.own_review(UINT64_OWNER, second_chart) is not None
    assert await history_for_review_id(database, owner_review_id) == []
    assert len(await history_for_review_id(database, other_review_id)) == 1
    assert len(await history_for_review_id(database, second_review_id)) == 1


@pytest.mark.asyncio
async def test_delete_all_removes_profile_reviews_and_history_but_not_other_owners(
    database,
):
    first_chart = await add_chart(database, 700011)
    second_chart = await add_chart(database, 700012)
    service = ChartReviewService(database)
    await service.submit(UINT64_OWNER, first_chart, reviewer_profile(), chart_review())
    await service.submit(UINT64_OWNER, second_chart, reviewer_profile(), chart_review())
    await service.submit(OTHER_OWNER, first_chart, reviewer_profile(), chart_review())
    owner_review_ids = [
        await review_id_for(database, UINT64_OWNER, chart_id)
        for chart_id in (first_chart, second_chart)
    ]
    other_review_id = await review_id_for(database, OTHER_OWNER, first_chart)

    await service.delete_all(UINT64_OWNER)

    assert await service.profile(UINT64_OWNER) is None
    assert await service.own_review(UINT64_OWNER, first_chart) is None
    assert await service.own_review(UINT64_OWNER, second_chart) is None
    assert await service.own_review(OTHER_OWNER, first_chart) is not None
    for review_id in owner_review_ids:
        assert await history_for_review_id(database, review_id) == []
    assert len(await history_for_review_id(database, other_review_id)) == 1


@pytest.mark.asyncio
async def test_public_summary_is_anonymous_and_available_from_one_review(database):
    chart_id = await add_chart(database, 700013)
    service = ChartReviewService(database)
    user_ids = [8_100_000_000_000_000_000 + offset for offset in range(5)]

    assert await service.public_summary(chart_id) == {
        "available": False,
        "minimum": 1,
    }
    for count, user_id in enumerate(user_ids, 1):
        await service.submit(
            user_id,
            chart_id,
            reviewer_profile(),
            chart_review(comment=f"private comment for {user_id}"),
        )
        current = await service.public_summary(chart_id)
        assert current["available"] is True
        assert current["count"] == count and current["minimum"] == 1
        assert all(
            group["count"] == count
            for groups in current["groups"].values()
            for group in groups
        )
    summary = await service.public_summary(chart_id)
    serialized = json.dumps(summary, ensure_ascii=False)

    assert summary["available"] is True
    assert summary["count"] == 5
    assert set(summary) == {
        "available",
        "count",
        "enjoyment",
        "difficulty",
        "groups",
        "minimum",
    }
    assert set(summary["groups"]) == {
        "rating_bucket",
        "primary_level",
        "experience_bucket",
    }
    assert all(
        set(group) == {"label", "count", "enjoyment", "difficulty"}
        for groups in summary["groups"].values()
        for group in groups
    )
    assert all(str(user_id) not in serialized for user_id in user_ids)
    assert "private comment" not in serialized
    assert "strengths" not in serialized and "weaknesses" not in serialized
    assert "體力" not in serialized and "鍵盤" not in serialized
    assert "discord_id" not in serialized
    assert "created_at" not in serialized and "updated_at" not in serialized


def split_profile(group):
    if group == "A":
        return reviewer_profile(
            rating_bucket=RATING_BUCKETS[3],
            primary_level="13",
            experience_bucket=EXPERIENCE_BUCKETS[3],
        )
    return reviewer_profile(
        rating_bucket=RATING_BUCKETS[4],
        primary_level="14",
        experience_bucket=EXPERIENCE_BUCKETS[2],
    )


@pytest.mark.asyncio
async def test_public_summary_includes_small_groups_and_complements(database):
    eight_chart = await add_chart(database, 700014)
    ten_chart = await add_chart(database, 700015)
    service = ChartReviewService(database)

    for offset in range(8):
        await service.submit(
            1_000_000_000_000_000_000 + offset,
            eight_chart,
            split_profile("A" if offset < 5 else "B"),
            chart_review(comment=f"eight-reviewer-{offset}"),
        )
    eight_summary = await service.public_summary(eight_chart)
    assert eight_summary["count"] == 8
    assert all(
        sorted(group["count"] for group in groups) == [3, 5]
        for groups in eight_summary["groups"].values()
    )

    for offset in range(10):
        await service.submit(
            1_100_000_000_000_000_000 + offset,
            ten_chart,
            split_profile("A" if offset < 5 else "B"),
            chart_review(comment=f"ten-reviewer-{offset}"),
        )
    ten_summary = await service.public_summary(ten_chart)
    assert ten_summary["count"] == 10
    for groups in ten_summary["groups"].values():
        assert [group["count"] for group in groups] == [5, 5]


@pytest.mark.asyncio
async def test_unknown_background_is_saved_without_fabricated_public_groups(database):
    chart_id = await add_chart(database, 700016)
    service = ChartReviewService(database)
    unknown = reviewer_profile(
        rating_bucket="未提供", primary_level="未提供", experience_bucket="未提供"
    )
    await service.submit(UINT64_OWNER, chart_id, unknown, chart_review())
    assert await service.profile(UINT64_OWNER) == unknown
    assert (await service.own_review(UINT64_OWNER, chart_id))["profile"] == unknown
    summary = await service.public_summary(chart_id)
    assert summary["available"] and summary["count"] == 1
    assert all(not groups for groups in summary["groups"].values())
