"""Add private player chart reviews and revision history.

Revision ID: 9d3a1f7c2b60
Revises: 4c6f8b1a0d92
"""

import re

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

revision = "9d3a1f7c2b60"
down_revision = "4c6f8b1a0d92"
branch_labels = None
depends_on = None

_REVIEW_TABLES = (
    "chart_reviewer_profiles",
    "chart_player_reviews",
    "chart_player_review_history",
)
_CHECK_PATTERN = re.compile(r"\bCHECK\s*\(([^()]*)\)", re.IGNORECASE)
_PARTIAL_SCHEMA_ERROR = (
    "Partial player review schema; repair explicitly before migration."
)
_SCHEMA_MISMATCH_ERROR = "Existing player review schema differs from migration."
_OFFLINE_DOWNGRADE_ERROR = "Offline review downgrade cannot verify table emptiness."
_INCOMPLETE_DOWNGRADE_ERROR = "Player review schema is incomplete; refusing downgrade."
_POPULATED_DOWNGRADE_ERROR = (
    "Preserve player review data; downgrade is allowed only for empty tables."
)


def _create_tables(operations):
    operations.create_table(
        "chart_reviewer_profiles",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column("rating_bucket", sa.String(), nullable=False),
        sa.Column("primary_level", sa.String(), nullable=False),
        sa.Column("experience_bucket", sa.String(), nullable=False),
        sa.Column("strengths_json", sa.String(), nullable=False),
        sa.Column("weaknesses_json", sa.String(), nullable=False),
        sa.Column("updated_at", sa.REAL(), nullable=False),
    )
    operations.create_table(
        "chart_player_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chart_id",
            sa.Integer(),
            sa.ForeignKey("chunirec_charts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "discord_id",
            sa.BigInteger(),
            sa.ForeignKey("chart_reviewer_profiles.discord_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enjoyment", sa.Integer(), nullable=False),
        sa.Column("perceived_difficulty", sa.Integer(), nullable=False),
        sa.Column("familiarity", sa.String(), nullable=False),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("comment", sa.String(), nullable=False),
        sa.Column("profile_snapshot_json", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.REAL(), nullable=False),
        sa.Column("updated_at", sa.REAL(), nullable=False),
        sa.UniqueConstraint("chart_id", "discord_id"),
        sa.CheckConstraint("enjoyment BETWEEN 1 AND 5"),
        sa.CheckConstraint("perceived_difficulty BETWEEN -2 AND 2"),
        sa.CheckConstraint("revision >= 1"),
    )
    operations.create_index(
        "ix_player_review_chart", "chart_player_reviews", ["chart_id"]
    )
    operations.create_table(
        "chart_player_review_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_id",
            sa.Integer(),
            sa.ForeignKey("chart_player_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("review_json", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.REAL(), nullable=False),
        sa.UniqueConstraint("review_id", "revision"),
    )


def _signature(connection, table):
    columns = [
        tuple(row)[1:6]
        for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')
    ]
    foreign_keys = sorted(
        tuple(row)[1:]
        for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')
    )
    indices = []
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
        name = row[1].replace('"', '""')
        columns_in_index = tuple(
            (index_row[1], index_row[2], index_row[3], index_row[4])
            for index_row in connection.exec_driver_sql(
                f'PRAGMA index_xinfo("{name}")'
            )
            if index_row[5]
        )
        indices.append((row[1], row[2], row[3], row[4], columns_in_index))
    indices.sort()
    checks = tuple(
        sorted(
            " ".join(check.split()).casefold()
            for check in _CHECK_PATTERN.findall(
                connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).scalar_one()
            )
        )
    )
    create_sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).scalar_one()
    table_options = (
        bool(re.search(r"\bWITHOUT\s+ROWID\b", create_sql, re.IGNORECASE)),
        bool(re.search(r"\bSTRICT\s*$", create_sql, re.IGNORECASE)),
    )
    triggers = tuple(
        sorted(
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                (table,),
            )
        )
    )
    return columns, foreign_keys, indices, checks, table_options, triggers


def _expected_signatures():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as reference:
            _create_tables(Operations(MigrationContext.configure(reference)))
            return {
                table: _signature(reference, table) for table in _REVIEW_TABLES
            }
    finally:
        engine.dispose()


def _existing_tables(connection):
    placeholders = ",".join("?" for _ in _REVIEW_TABLES)
    objects = {
        row["name"]: row["type"]
        for row in connection.exec_driver_sql(
            f"SELECT type,name FROM sqlite_master WHERE name IN ({placeholders})",
            _REVIEW_TABLES,
        ).mappings()
    }
    if any(object_type != "table" for object_type in objects.values()):
        raise RuntimeError(_SCHEMA_MISMATCH_ERROR)
    return set(objects)


def upgrade():
    if op.get_context().as_sql:
        _create_tables(op)
        return

    connection = op.get_bind()
    existing = _existing_tables(connection)
    if existing and existing != set(_REVIEW_TABLES):
        raise RuntimeError(_PARTIAL_SCHEMA_ERROR)
    if existing:
        expected = _expected_signatures()
        for table in _REVIEW_TABLES:
            if _signature(connection, table) != expected[table]:
                raise RuntimeError(_SCHEMA_MISMATCH_ERROR)
        return

    _create_tables(op)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError(_OFFLINE_DOWNGRADE_ERROR)

    connection = op.get_bind()
    existing = _existing_tables(connection)
    if existing != set(_REVIEW_TABLES):
        raise RuntimeError(_INCOMPLETE_DOWNGRADE_ERROR)
    expected = _expected_signatures()
    for table in _REVIEW_TABLES:
        if _signature(connection, table) != expected[table]:
            raise RuntimeError(_SCHEMA_MISMATCH_ERROR)
    if any(
        connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one()
        for table in _REVIEW_TABLES
    ):
        raise RuntimeError(_POPULATED_DOWNGRADE_ERROR)

    op.drop_table("chart_player_review_history")
    op.drop_table("chart_player_reviews")
    op.drop_table("chart_reviewer_profiles")
