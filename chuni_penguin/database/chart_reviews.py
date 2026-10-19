"""Private ownership and versioned, publicly anonymous player chart reviews."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UInt64Integer

REVIEW_TABLES = (
    "chart_reviewer_profiles",
    "chart_player_reviews",
    "chart_player_review_history",
)


class ChartReviewerProfile(Base):
    __tablename__ = "chart_reviewer_profiles"

    discord_id: Mapped[int] = mapped_column(UInt64Integer(), primary_key=True)
    rating_bucket: Mapped[str]
    primary_level: Mapped[str]
    experience_bucket: Mapped[str]
    strengths_json: Mapped[str]
    weaknesses_json: Mapped[str]
    updated_at: Mapped[datetime]


class ChartPlayerReview(Base):
    __tablename__ = "chart_player_reviews"
    __table_args__ = (
        UniqueConstraint("chart_id", "discord_id"),
        CheckConstraint("enjoyment BETWEEN 1 AND 5"),
        CheckConstraint("perceived_difficulty BETWEEN -2 AND 2"),
        CheckConstraint("revision >= 1"),
        Index("ix_player_review_chart", "chart_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chart_id: Mapped[int] = mapped_column(
        ForeignKey("chunirec_charts.id", ondelete="RESTRICT")
    )
    discord_id: Mapped[int] = mapped_column(
        UInt64Integer(),
        ForeignKey("chart_reviewer_profiles.discord_id", ondelete="CASCADE"),
    )
    enjoyment: Mapped[int]
    perceived_difficulty: Mapped[int]
    familiarity: Mapped[str]
    goal: Mapped[str]
    comment: Mapped[str]
    profile_snapshot_json: Mapped[str]
    revision: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ChartPlayerReviewHistory(Base):
    __tablename__ = "chart_player_review_history"
    __table_args__ = (UniqueConstraint("review_id", "revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("chart_player_reviews.id", ondelete="CASCADE")
    )
    revision: Mapped[int]
    review_json: Mapped[str]
    recorded_at: Mapped[datetime]
