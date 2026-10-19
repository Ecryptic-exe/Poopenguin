"""Independent source observations and confirmed chart annotation queries."""

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from chuni_penguin.application.errors import ServiceUnavailable
from chuni_penguin.context import PenguinContext
from chuni_penguin.identities import MappingError
from chuni_penguin.providers.models import CatalogRequest
from chuni_penguin.providers.registry import ProviderError
from chuni_penguin.repositories.chart_knowledge import KnowledgeError, ManualAnnotation
from chuni_penguin.repositories.semantic_knowledge import SemanticObservation

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


def _safe(value: str, limit: int = 120) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value[:limit]))


def _row(row: ManualAnnotation) -> str:
    tags = ", ".join(_safe(tag, 40) for tag in row.tags[:8]) or "Unknown"
    return f"{_safe(row.title or 'Unknown')} / {_safe(row.difficulty_label or 'Unknown')}\nTags: {tags}\nRow: {row.row_id}"


def _semantic_row(row: SemanticObservation) -> str:
    tags = ", ".join(_safe(tag, 40) for tag in row.tags[:8]) or "None"
    issues = ", ".join(row.issues) or "none"
    return (
        f"{_safe(row.title)} / {row.difficulty_label}; v3 {row.role} / {row.status}\n"
        f"Raw tags: {tags}; summary: {_safe(row.summary or 'None', 200)}\n"
        f"Current at snapshot read: {row.current}; text evidence verified: {row.evidence_verified}; issues: {issues}\n"
        "Input packet and model execution unverified; canonical association unresolved.\n"
        f"Imported: {row.imported_at.isoformat()}\nRow: {_safe(row.row_id, 180)}"
    )


class AnnotationsCog(commands.Cog, name="Annotations"):
    def __init__(self, bot: "ChuniBot"):
        self.application = bot.services
        self.knowledge = bot.services.chart_knowledge

    @commands.hybrid_group("annotations")
    async def annotations(self, ctx: PenguinContext):
        """Read local annotation sources and reviewed chart associations."""
        await ctx.send_help(ctx.command)

    @annotations.command("sources")
    async def sources(self, ctx: PenguinContext):
        """Show local annotation versions and their provenance limits."""
        lines = []
        for repo in self.knowledge.repositories:
            origin = repo.origin
            lines.append(
                f"{origin.namespace}: {len(repo.rows)} rows; artifact {origin.version}\nMethod: {origin.generation_method}; workbook claim: {_safe(origin.claimed_workbook)}\nObserved: {origin.observed_at.isoformat()}; original dates/worksheet row unknown."
            )
        for repo in self.knowledge.semantic_repositories:
            origin = repo.origin
            lines.append(
                f"{origin.namespace}: {len(repo.rows)} analyses; snapshot {origin.version}\n"
                f"Catalogue: {_safe(origin.catalogue_id)} / {_safe(origin.region)} / {_safe(origin.game_version)}\n"
                f"Method: {origin.generation_method}; observed: {origin.observed_at.isoformat()}\n"
                "Model execution unverified; source observations only."
            )
        lines.extend(
            f"Unavailable source: {name}; {reason}"
            for name, reason in self.knowledge.source_failures
        )
        await ctx.send(
            "\n\n".join(lines)[:1950], allowed_mentions=discord.AllowedMentions.none()
        )

    @annotations.command("find")
    async def find(self, ctx: PenguinContext, *, query: str):
        """Find source rows; results do not assert a canonical chart mapping."""
        rows = self.knowledge.search(query)
        semantic_rows = self.knowledge.search_semantics(query)
        content = (
            "Source observations; canonical mapping not checked. Legacy difficulty evidence is unverified.\n\n"
            + ("\n\n".join(_row(row) for row in rows) or "No matching rows.")
        )
        if semantic_rows:
            content = (
                "Source observations; canonical mapping not checked.\n\n"
                + _semantic_row(semantic_rows[0])
                + (
                    "\n\nLegacy manual observations (difficulty evidence unverified):\n"
                    + "\n\n".join(_row(row) for row in rows[:2])
                    if rows
                    else ""
                )
            )
        await ctx.send(content[:1950], allowed_mentions=discord.AllowedMentions.none())

    @annotations.command("row")
    async def row(self, ctx: PenguinContext, row_id: str):
        """Read a precise artifact row without guessing a song/chart identity."""
        item = self.knowledge.rows.get(row_id)
        if item is None:
            semantic = self.knowledge.semantic_rows.get(row_id)
            if semantic is not None:
                await ctx.send(
                    _semantic_row(semantic)[:1950],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            await ctx.send("Unknown annotation row.")
            return
        content = (
            "Source row; canonical mapping not checked.\n"
            + _row(item)
            + "\nRaw legacy record (may be truncated):\n"
            + _safe(item.raw_record_json, 1600)
        )
        await ctx.send(content[:1950], allowed_mentions=discord.AllowedMentions.none())

    @annotations.command("chart")
    async def chart(
        self,
        ctx: PenguinContext,
        provider: str,
        song_id: str,
        difficulty: str,
        variant: str = "default",
        chart_id: str | None = None,
    ):
        """Read reviewed annotations through a current confirmed chart mapping."""
        descriptor = next(
            (d for d in self.application.providers.descriptors if d.key == provider),
            None,
        )
        if descriptor is None:
            await ctx.send("The selected provider is unavailable.")
            return
        try:
            request = CatalogRequest(descriptor.scope, provider_key=provider)
            selected = await self.application.catalog_query.chart(
                song_id, difficulty, request, variant=variant, chart_id=chart_id
            )
            mapped = await self.application.mapped_catalog.chart(
                selected.chart.ref, request
            )
            rows = self.knowledge.for_chart(mapped)
        except (MappingError, ProviderError, ServiceUnavailable, KnowledgeError):
            await ctx.send(
                "No current confirmed chart mapping or matching reviewed annotation source is available."
            )
            return
        if not rows:
            await ctx.send("No reviewed annotations are bound to this chart.")
            return
        content = (
            f"Canonical Chart ID: {mapped.mapping.chart.ref.chart_id}; Song ID: {mapped.mapping.song.ref.song_id}\nSource: {provider} / {descriptor.scope.region} / {descriptor.scope.game_version}\n"
            + "\n\n".join(
                _row(result.annotation)
                + f"\nReview: {_safe(result.evidence.reference)}; mapping revision {result.mapping_revision}"
                for result in rows
            )
        )
        await ctx.send(content[:1950], allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: "ChuniBot"):
    await bot.add_cog(AnnotationsCog(bot))
