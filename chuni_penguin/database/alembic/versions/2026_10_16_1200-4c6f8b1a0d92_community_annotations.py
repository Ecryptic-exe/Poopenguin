"""Add versioned community chart annotations without changing catalog/player rows.

Revision ID: 4c6f8b1a0d92
Revises: 7e2a0c9d4b31
"""

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

revision = "4c6f8b1a0d92"
down_revision = "7e2a0c9d4b31"
branch_labels = None
depends_on = None


def _create_tables(operations):
    operations.create_table(
        "chart_annotations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_snapshot_id",
            sa.String(),
            sa.ForeignKey("source_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_sheet", sa.String(), nullable=False),
        sa.Column("source_excel_row", sa.Integer(), nullable=False),
        sa.Column(
            "chart_id",
            sa.Integer(),
            sa.ForeignKey("chunirec_charts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "song_id",
            sa.Integer(),
            sa.ForeignKey("chunirec_songs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("difficulty", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("genre", sa.String(), nullable=True),
        sa.Column("source_const", sa.Float(), nullable=True),
        sa.Column("source_notes", sa.Integer(), nullable=True),
        sa.Column("sss_stars", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("flags_json", sa.String(), nullable=False),
        sa.Column("links_json", sa.String(), nullable=False),
        sa.Column("raw_cells_json", sa.String(), nullable=False),
        sa.Column("review_json", sa.String(), nullable=False),
        sa.Column("match_method", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.REAL(), nullable=False),
        sa.UniqueConstraint("source_snapshot_id", "source_row"),
        sa.UniqueConstraint("source_snapshot_id", "source_sheet", "source_excel_row"),
        sa.CheckConstraint("source_row >= 0 AND source_excel_row >= 1"),
        sa.CheckConstraint("difficulty IN ('BAS','ADV','EXP','MAS','ULT')"),
        sa.CheckConstraint("match_method IN ('automatic_exact','agent_reviewed')"),
        sa.CheckConstraint("sss_stars IS NULL OR sss_stars BETWEEN 1 AND 5"),
        sa.CheckConstraint("source_notes IS NULL OR source_notes >= 0"),
        sa.CheckConstraint("active IN (0,1)"),
    )
    operations.create_index(
        "ix_annotation_active_chart",
        "chart_annotations",
        ["active", "chart_id", "namespace"],
    )
    operations.create_table(
        "chart_annotation_tags",
        sa.Column(
            "annotation_id",
            sa.Integer(),
            sa.ForeignKey("chart_annotations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("normalized_tag", sa.String(), primary_key=True),
        sa.Column("tag", sa.String(), nullable=False),
    )
    operations.create_index(
        "ix_chart_annotation_tags_normalized_tag",
        "chart_annotation_tags",
        ["normalized_tag"],
    )


def _signature(connection, table):
    inspector = sa.inspect(connection)
    return (
        [
            (c["name"], str(c["type"]), c["nullable"], c["primary_key"], c["default"])
            for c in inspector.get_columns(table)
        ],
        sorted(
            (
                tuple(f["constrained_columns"]),
                f["referred_table"],
                tuple(f["referred_columns"]),
                tuple(sorted(f["options"].items())),
            )
            for f in inspector.get_foreign_keys(table)
        ),
        sorted(
            tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)
        ),
        sorted(
            (tuple(i["column_names"]), bool(i["unique"]))
            for i in inspector.get_indexes(table)
        ),
        sorted(
            " ".join(c["sqltext"].split())
            for c in inspector.get_check_constraints(table)
        ),
    )


def upgrade():
    if not op.get_context().as_sql:
        tables = {"chart_annotations", "chart_annotation_tags"}
        existing = set(sa.inspect(op.get_bind()).get_table_names()).intersection(tables)
        if existing and existing != tables:
            raise RuntimeError(
                "Partial annotation schema; repair explicitly before migration."
            )
        if existing:
            # Unversioned local databases install these two tables during import.
            # Accept only the exact frozen migration schema; never stamp unknown tables.
            engine = sa.create_engine("sqlite://")
            try:
                with engine.begin() as reference:
                    _create_tables(Operations(MigrationContext.configure(reference)))
                    for table in tables:
                        if _signature(reference, table) != _signature(
                            op.get_bind(), table
                        ):
                            raise RuntimeError(
                                "Existing annotation schema differs from migration."
                            )
            finally:
                engine.dispose()
            return
    _create_tables(op)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Annotation downgrade requires an online emptiness check.")
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM chart_annotations")):
        raise RuntimeError(
            "Preserve annotation history; use rollback-annotations to deactivate a batch."
        )
    op.drop_table("chart_annotation_tags")
    op.drop_table("chart_annotations")
