"""Shared database read path for Song annotations and tag searches."""

import json

from sqlalchemy import and_, select

from chuni_penguin.database import Chart, ChartAnnotation, ChartAnnotationTag
from chuni_penguin.database.annotations import normalize_tag


class ChartAnnotationRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _query():
        # A changed canonical identity must never silently inherit stale annotations.
        return (
            select(ChartAnnotation)
            .join(
                Chart,
                and_(
                    Chart.id == ChartAnnotation.chart_id,
                    Chart.song_id == ChartAnnotation.song_id,
                    Chart.difficulty == ChartAnnotation.difficulty,
                ),
            )
            .where(ChartAnnotation.active.is_(True))
        )

    async def _rows(self, statement):
        async with self.sessions() as session:
            annotations = list(
                await session.scalars(
                    statement.order_by(
                        ChartAnnotation.namespace, ChartAnnotation.source_row
                    )
                )
            )
            if not annotations:
                return []
            tags = {}
            for row in await session.execute(
                select(
                    ChartAnnotationTag.annotation_id,
                    ChartAnnotationTag.tag,
                )
                .where(
                    ChartAnnotationTag.annotation_id.in_([a.id for a in annotations])
                )
                .order_by(ChartAnnotationTag.normalized_tag)
            ):
                tags.setdefault(row.annotation_id, []).append(row.tag)
            result = []
            for annotation in annotations:
                result.append(
                    {
                        "source_row": annotation.source_row,
                        "sheet": annotation.source_sheet,
                        "excel_row": annotation.source_excel_row,
                        "chart_id": annotation.chart_id,
                        "song_id": annotation.song_id,
                        "difficulty": annotation.difficulty,
                        "title": annotation.title,
                        "genre": annotation.genre,
                        "source_const": annotation.source_const,
                        "source_notes": annotation.source_notes,
                        "sss_stars": annotation.sss_stars,
                        "note": annotation.note,
                        "tags": tags.get(annotation.id, []),
                        "flags": json.loads(annotation.flags_json),
                        "links": json.loads(annotation.links_json),
                        "match_method": annotation.match_method,
                        "source_snapshot_id": annotation.source_snapshot_id,
                    }
                )
            return result

    async def for_chart(self, chart):
        return await self._rows(
            self._query().where(
                ChartAnnotation.chart_id == chart.id,
                ChartAnnotation.song_id == chart.song_id,
                ChartAnnotation.difficulty == chart.difficulty,
            )
        )

    def _tag_query(self, tag):
        value = normalize_tag(tag)
        statement = self._query().where(
            ChartAnnotation.id.in_(
                select(ChartAnnotationTag.annotation_id).where(
                    ChartAnnotationTag.normalized_tag.contains(value, autoescape=True)
                )
            )
        )
        return statement if value else statement.where(False)

    async def search(self, tag):
        return await self._rows(self._tag_query(tag))

    async def chart_ids(self, tag):
        async with self.sessions() as session:
            return set(
                await session.scalars(
                    self._tag_query(tag).with_only_columns(ChartAnnotation.chart_id)
                )
            )
