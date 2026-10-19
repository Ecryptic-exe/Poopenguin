"""Versioned community annotations, separate from the canonical chart catalog."""

import unicodedata
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

ANNOTATION_TABLES = ("chart_annotations", "chart_annotation_tags")


def normalize_tag(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


class ChartAnnotation(Base):
    __tablename__ = "chart_annotations"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "source_row"),
        UniqueConstraint("source_snapshot_id", "source_sheet", "source_excel_row"),
        CheckConstraint("source_row >= 0 AND source_excel_row >= 1"),
        CheckConstraint("difficulty IN ('BAS','ADV','EXP','MAS','ULT')"),
        CheckConstraint("match_method IN ('automatic_exact','agent_reviewed')"),
        CheckConstraint("sss_stars IS NULL OR sss_stars BETWEEN 1 AND 5"),
        CheckConstraint("source_notes IS NULL OR source_notes >= 0"),
        CheckConstraint("active IN (0,1)"),
        Index("ix_annotation_active_chart", "active", "chart_id", "namespace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT")
    )
    namespace: Mapped[str]
    source_row: Mapped[int]
    source_sheet: Mapped[str]
    source_excel_row: Mapped[int]
    chart_id: Mapped[int] = mapped_column(
        ForeignKey("chunirec_charts.id", ondelete="RESTRICT")
    )
    song_id: Mapped[int] = mapped_column(
        ForeignKey("chunirec_songs.id", ondelete="RESTRICT")
    )
    difficulty: Mapped[str]
    title: Mapped[str]
    genre: Mapped[str | None]
    source_const: Mapped[float | None]
    source_notes: Mapped[int | None]
    sss_stars: Mapped[int | None]
    note: Mapped[str | None]
    flags_json: Mapped[str]
    links_json: Mapped[str]
    raw_cells_json: Mapped[str]
    review_json: Mapped[str]
    match_method: Mapped[str]
    active: Mapped[bool]
    imported_at: Mapped[datetime]


class ChartAnnotationTag(Base):
    __tablename__ = "chart_annotation_tags"

    annotation_id: Mapped[int] = mapped_column(
        ForeignKey("chart_annotations.id", ondelete="CASCADE"), primary_key=True
    )
    normalized_tag: Mapped[str] = mapped_column(primary_key=True, index=True)
    tag: Mapped[str]
