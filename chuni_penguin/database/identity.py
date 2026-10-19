"""Additive external identities. Candidate/decision events preserve review history."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

MAPPING_TABLES = (
    "source_snapshots",
    "external_song_ids",
    "external_chart_ids",
    "id_mapping_events",
)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(primary_key=True)
    provider_key: Mapped[str]
    namespace: Mapped[str]
    scope_key: Mapped[str]
    revision: Mapped[str]
    payload_sha256: Mapped[str]
    provenance_json: Mapped[str]
    observed_at: Mapped[datetime]
    imported_at: Mapped[datetime]


class ExternalSongID(Base):
    __tablename__ = "external_song_ids"
    __table_args__ = (
        UniqueConstraint("namespace", "external_id", "scope_key"),
        CheckConstraint("status IN ('unresolved','candidate','confirmed')"),
        CheckConstraint(
            "(status = 'confirmed' AND song_id IS NOT NULL) OR (status != 'confirmed' AND song_id IS NULL)"
        ),
        CheckConstraint("revision >= 1"),
        CheckConstraint(
            "length(namespace) > 0 AND length(external_id) > 0 AND length(scope_key) > 0"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str]
    external_id: Mapped[str]
    scope_key: Mapped[str]
    song_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunirec_songs.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    revision: Mapped[int]
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ExternalChartID(Base):
    __tablename__ = "external_chart_ids"
    __table_args__ = (
        UniqueConstraint("external_song_id", "external_key"),
        CheckConstraint("status IN ('unresolved','candidate','confirmed')"),
        CheckConstraint(
            "(status = 'confirmed' AND chart_id IS NOT NULL) OR (status != 'confirmed' AND chart_id IS NULL)"
        ),
        CheckConstraint("revision >= 1"),
        CheckConstraint(
            "length(external_key) > 0 AND length(difficulty) > 0 AND length(variant) > 0"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_song_id: Mapped[int] = mapped_column(
        ForeignKey("external_song_ids.id", ondelete="RESTRICT")
    )
    external_key: Mapped[str]
    difficulty: Mapped[str]
    variant: Mapped[str]
    chart_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunirec_charts.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    revision: Mapped[int]
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class IDMappingEvent(Base):
    __tablename__ = "id_mapping_events"
    __table_args__ = (
        UniqueConstraint("external_song_id", "revision"),
        UniqueConstraint("external_chart_id", "revision"),
        CheckConstraint(
            "(external_song_id IS NOT NULL AND external_chart_id IS NULL) OR (external_song_id IS NULL AND external_chart_id IS NOT NULL)"
        ),
        CheckConstraint("action IN ('observed','candidate','confirmed','revoked')"),
        CheckConstraint("revision >= 1"),
        CheckConstraint(
            "(action IN ('observed','revoked') AND song_id IS NULL AND chart_id IS NULL) OR (action IN ('candidate','confirmed') AND ((external_song_id IS NOT NULL AND song_id IS NOT NULL AND chart_id IS NULL) OR (external_chart_id IS NOT NULL AND chart_id IS NOT NULL AND song_id IS NULL)))"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_song_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_song_ids.id", ondelete="RESTRICT")
    )
    external_chart_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_chart_ids.id", ondelete="RESTRICT")
    )
    revision: Mapped[int]
    action: Mapped[str]
    song_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunirec_songs.id", ondelete="RESTRICT")
    )
    chart_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunirec_charts.id", ondelete="RESTRICT")
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT")
    )
    evidence_json: Mapped[str]
    created_at: Mapped[datetime]
