"""Validated owner edits and bounded, anonymous aggregates; no public free text."""

import json
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import delete, select

from chuni_penguin.database import (
    Chart,
    ChartPlayerReview,
    ChartPlayerReviewHistory,
    ChartReviewerProfile,
)

RATING_BUCKETS = (
    "未定級",
    "未滿 12",
    "12–13.99",
    "14–14.99",
    "15–15.49",
    "15.5–15.99",
    "16–16.49",
    "16.5–16.99",
    "17 以上",
    "未提供",
)
PRIMARY_LEVELS = (
    "11 以下",
    "12",
    "12+",
    "13",
    "13+",
    "14",
    "14+",
    "15",
    "15+",
    "未提供",
)
EXPERIENCE_BUCKETS = (
    "未滿 3 個月",
    "3–12 個月",
    "1–3 年",
    "3–5 年",
    "5 年以上",
    "未提供",
)
SKILLS = ("鍵盤", "交互", "縱連", "滑鍵", "擦鍵", "Air", "體力", "讀譜")
FAMILIARITIES = ("初次遊玩", "2–10 次", "11 次以上")
GOALS = ("通關", "SS", "SSS", "SSS+", "FC", "AJ", "理論值")
DIFFICULTY_LABELS = {
    -2: "明顯偏易",
    -1: "略易",
    0: "符合定數",
    1: "略難",
    2: "明顯偏難",
}
# Temporarily show aggregates as soon as the first review is submitted.
MIN_PUBLIC_GROUP = 1


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _profile(data):
    for key, allowed in (
        ("rating_bucket", RATING_BUCKETS),
        ("primary_level", PRIMARY_LEVELS),
        ("experience_bucket", EXPERIENCE_BUCKETS),
    ):
        if data.get(key) not in allowed:
            raise ValueError("請完整選擇 Rating 區間、主力等級與遊玩經驗。")
    result = {
        key: data[key]
        for key in ("rating_bucket", "primary_level", "experience_bucket")
    }
    for key in ("strengths", "weaknesses"):
        value = data.get(key, [])
        if not isinstance(value, (list, tuple)) or any(
            item not in SKILLS for item in value
        ):
            raise ValueError("請從提供的項目選擇擅長與弱項。")
        result[key] = list(dict.fromkeys(value))
    return result


def _review(data):
    if type(data.get("enjoyment")) is not int or not 1 <= data["enjoyment"] <= 5:
        raise ValueError("喜好程度需為 1–5。")
    if (
        type(data.get("perceived_difficulty")) is not int
        or data["perceived_difficulty"] not in DIFFICULTY_LABELS
    ):
        raise ValueError("請選擇體感難度。")
    if data.get("familiarity") not in FAMILIARITIES or data.get("goal") not in GOALS:
        raise ValueError("請選擇熟悉程度與目標。")
    comment = data.get("comment")
    if not isinstance(comment, str) or not 1 <= len(comment.strip()) <= 1000:
        raise ValueError("評論需為 1–1000 字。")
    return {
        "enjoyment": data["enjoyment"],
        "perceived_difficulty": data["perceived_difficulty"],
        "familiarity": data["familiarity"],
        "goal": data["goal"],
        "comment": comment.strip(),
    }


def _identity(user_id, chart_id=None):
    if type(user_id) is not int or not 0 < user_id < 2**64:
        raise ValueError("無法識別目前使用者。")
    if chart_id is not None and (type(chart_id) is not int or chart_id <= 0):
        raise ValueError("請先選擇有效譜面。")


def _profile_dict(row):
    return {
        "rating_bucket": row.rating_bucket,
        "primary_level": row.primary_level,
        "experience_bucket": row.experience_bucket,
        "strengths": json.loads(row.strengths_json),
        "weaknesses": json.loads(row.weaknesses_json),
    }


class ChartReviewService:
    def __init__(self, database):
        self.database = database

    async def profile(self, user_id):
        _identity(user_id)
        async with self.database.mapping_read_sessionmaker() as session:
            row = await session.get(ChartReviewerProfile, user_id)
            return None if row is None else _profile_dict(row)

    async def own_review(self, user_id, chart_id):
        _identity(user_id, chart_id)
        async with self.database.mapping_read_sessionmaker() as session:
            row = await session.scalar(
                select(ChartPlayerReview).where(
                    ChartPlayerReview.discord_id == user_id,
                    ChartPlayerReview.chart_id == chart_id,
                )
            )
            if row is None:
                return None
            return {
                key: getattr(row, key)
                for key in (
                    "enjoyment",
                    "perceived_difficulty",
                    "familiarity",
                    "goal",
                    "comment",
                    "revision",
                )
            } | {"profile": json.loads(row.profile_snapshot_json)}

    async def submit(self, user_id, chart_id, profile, review, *, expected_revision=0):
        _identity(user_id, chart_id)
        profile, review = _profile(profile), _review(review)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("評價版本無效，請重新開啟表單。")

        async def transaction(session):
            if await session.get(Chart, chart_id) is None:
                raise ValueError("譜面已不存在，請重新搜尋。")
            row = await session.scalar(
                select(ChartPlayerReview).where(
                    ChartPlayerReview.discord_id == user_id,
                    ChartPlayerReview.chart_id == chart_id,
                )
            )
            if (row.revision if row else 0) != expected_revision:
                raise ValueError("這份評價已在另一個視窗更新，請重新開啟後再修改。")
            now = datetime.now(UTC)
            background = await session.get(ChartReviewerProfile, user_id)
            if background is None:
                background = ChartReviewerProfile(discord_id=user_id)
                session.add(background)
            for key in ("rating_bucket", "primary_level", "experience_bucket"):
                setattr(background, key, profile[key])
            background.strengths_json = _json(profile["strengths"])
            background.weaknesses_json = _json(profile["weaknesses"])
            background.updated_at = now
            await session.flush()
            if row is None:
                row = ChartPlayerReview(
                    chart_id=chart_id, discord_id=user_id, created_at=now, revision=0
                )
                session.add(row)
            for key, value in review.items():
                setattr(row, key, value)
            row.profile_snapshot_json = _json(profile)
            row.revision += 1
            row.updated_at = now
            await session.flush()
            session.add(
                ChartPlayerReviewHistory(
                    review_id=row.id,
                    revision=row.revision,
                    review_json=_json({**review, "profile": profile}),
                    recorded_at=now,
                )
            )
            return row.revision

        return await self.database.writer.execute_fn(transaction)

    async def delete(self, user_id, chart_id):
        _identity(user_id, chart_id)

        async def transaction(session):
            result = await session.execute(
                delete(ChartPlayerReview).where(
                    ChartPlayerReview.discord_id == user_id,
                    ChartPlayerReview.chart_id == chart_id,
                )
            )
            return bool(result.rowcount)

        return await self.database.writer.execute_fn(transaction)

    async def delete_all(self, user_id):
        _identity(user_id)
        await self.database.writer.execute(
            delete(ChartReviewerProfile).where(
                ChartReviewerProfile.discord_id == user_id
            )
        )

    async def public_summary(self, chart_id):
        if type(chart_id) is not int or chart_id <= 0:
            raise ValueError("請先選擇有效譜面。")
        async with self.database.mapping_read_sessionmaker() as session:
            # Deliberately never load public comments, user IDs, dates or full profiles.
            rows = list(
                await session.execute(
                    select(
                        ChartPlayerReview.enjoyment,
                        ChartPlayerReview.perceived_difficulty,
                        ChartPlayerReview.profile_snapshot_json,
                    ).where(ChartPlayerReview.chart_id == chart_id)
                )
            )
        if len(rows) < MIN_PUBLIC_GROUP:
            return {"available": False, "minimum": MIN_PUBLIC_GROUP}
        groups = {}
        for dimension in ("rating_bucket", "primary_level", "experience_bucket"):
            values = {}
            for row in rows:
                key = json.loads(row.profile_snapshot_json)[dimension]
                values.setdefault(key, []).append(row)
            groups[dimension] = [
                {
                    "label": label,
                    "count": len(members),
                    "enjoyment": round(
                        sum(row.enjoyment for row in members) / len(members), 2
                    ),
                    "difficulty": dict(
                        Counter(row.perceived_difficulty for row in members)
                    ),
                }
                for label, members in sorted(values.items())
                if label != "未提供"
                if len(members) >= MIN_PUBLIC_GROUP
                and (
                    len(rows) == len(members)
                    or len(rows) - len(members) >= MIN_PUBLIC_GROUP
                )
            ]
        return {
            "available": True,
            "count": len(rows),
            "enjoyment": round(sum(row.enjoyment for row in rows) / len(rows), 2),
            "difficulty": dict(Counter(row.perceived_difficulty for row in rows)),
            "groups": groups,
            "minimum": MIN_PUBLIC_GROUP,
        }
