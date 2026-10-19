"""Opt-in source catalog commands; no canonical-ID assumptions or Cog dependencies."""

from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import escape_markdown, escape_mentions

from chuni_penguin.context import PenguinContext
from chuni_penguin.identities import CatalogLookupError
from chuni_penguin.providers.models import CatalogChart, CatalogRequest, CatalogSnapshot
from chuni_penguin.providers.registry import ProviderError

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


def _text(value: object, limit: int = 180) -> str:
    escaped = escape_mentions(
        escape_markdown(str(value).replace("\n", " ").replace("\r", " "))
    )
    return escaped[:limit] or "Unknown"


def _embed(snapshot: CatalogSnapshot, title: str) -> discord.Embed:
    origin = snapshot.provenance
    embed = discord.Embed(title=_text(title, 200))
    embed.description = (
        f"Source: {_text(origin.provider_key, 60)} / {_text(origin.source_namespace, 60)}\n"
        f"Scope: {_text(origin.scope.region, 60)} / {_text(origin.scope.game_version, 60)}\n"
        f"Data version: {_text(origin.data_version or 'Unknown', 80)}\n"
        f"Observed ({_text(origin.observation_kind, 50)}): {origin.observed_at.isoformat()}\n"
        f"Source updated: {origin.source_updated_at.isoformat() if origin.source_updated_at else 'Unknown'}\n"
        f"Revision: {_text(origin.revision, 24)} | Source issues: {len(snapshot.issues)}"
        + (" | Stale" if snapshot.stale else "")
    )
    embed.set_footer(
        text="IDs belong to this source. Canonical mapping has not been checked."
    )
    return embed


def _chart_text(chart: CatalogChart) -> str:
    ref = chart.ref
    return (
        f"{_text(ref.difficulty, 20)} / {_text(ref.variant, 30)}: "
        f"level {_text(chart.level or 'Unknown', 20)}, constant {_text(chart.constant if chart.constant is not None else 'Unknown', 20)}"
        + (f"; chart ID {_text(ref.chart_id, 80)}" if ref.chart_id is not None else "")
    )


class ProviderCatalogCog(commands.Cog, name="ProviderCatalog"):
    def __init__(self, bot: "ChuniBot"):
        self.query = bot.services.catalog_query

    def _request(self, key: str) -> CatalogRequest:
        descriptor = next(
            (d for d in self.query.providers.descriptors if d.key == key), None
        )
        if descriptor is None:
            msg = "Unknown source. Use catalog sources to see configured providers."
            raise commands.BadArgument(msg)
        return CatalogRequest(descriptor.scope, provider_key=key)

    @commands.hybrid_group("catalog")
    async def catalog(self, ctx: PenguinContext):
        """Read an explicitly selected source catalog."""
        await ctx.send_help(ctx.command)

    @catalog.command("sources")
    async def sources(self, ctx: PenguinContext):
        """List configured sources and their exact region/version scopes."""
        embed = discord.Embed(title="Catalog sources")
        states = self.query.providers.status()
        descriptors = self.query.providers.descriptors
        for descriptor in descriptors[:10]:
            state = states[descriptor.key]
            embed.add_field(
                name=_text(descriptor.key, 60),
                value=f"Namespace: {_text(descriptor.source_namespace, 60)}\n"
                f"{_text(descriptor.scope.region, 60)} / {_text(descriptor.scope.game_version, 60)}\n"
                f"Status: {_text(state['state'], 30)}",
                inline=False,
            )
        embed.set_footer(
            text=f"{len(descriptors)} configured sources. Queries use one source; no automatic fallback."
        )
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @catalog.command("find")
    async def find(self, ctx: PenguinContext, source: str, *, title: str):
        """Find up to five source songs by title; duplicate titles stay separate."""
        request = self._request(source)
        await ctx.defer()
        try:
            result = await self.query.search(title, request)
        except (ProviderError, ValueError):
            await self._failure(ctx)
            return
        embed = _embed(result.snapshot, "Catalog title search")
        for song in result.songs:
            embed.add_field(
                name=_text(song.title, 200),
                value=f"Song ID: {_text(song.ref.id)}\nArtist: {_text(song.artist or 'Unknown')}",
                inline=False,
            )
        embed.add_field(
            name="Matches",
            value=f"Showing {len(result.songs)} of {result.total}.",
            inline=False,
        )
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @catalog.command("song")
    async def song(self, ctx: PenguinContext, source: str, song_id: str):
        """Read one exact source song ID, preserving its distinct charts."""
        request = self._request(source)
        await ctx.defer()
        try:
            result = await self.query.song(song_id, request)
        except (ProviderError, CatalogLookupError):
            await self._failure(ctx)
            return
        embed = _embed(result.snapshot, result.song.title)
        embed.add_field(
            name="Song",
            value=f"ID: {_text(result.song.ref.id)}\nArtist: {_text(result.song.artist or 'Unknown')}",
            inline=False,
        )
        for chart in result.song.charts[:10]:
            embed.add_field(name="Chart", value=_chart_text(chart), inline=False)
        if len(result.song.charts) > 10:
            embed.add_field(
                name="More charts",
                value=f"{len(result.song.charts) - 10} not shown; use catalog chart.",
            )
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @catalog.command("chart")
    async def chart(
        self,
        ctx: PenguinContext,
        source: str,
        song_id: str,
        difficulty: str,
        variant: str = "default",
        chart_id: str | None = None,
    ):
        """Read an exact difficulty/variant; give chart_id if the source is ambiguous."""
        request = self._request(source)
        await ctx.defer()
        try:
            result = await self.query.chart(
                song_id, difficulty.upper(), request, variant=variant, chart_id=chart_id
            )
        except (ProviderError, CatalogLookupError):
            await self._failure(ctx)
            return
        embed = _embed(result.snapshot, result.song.title)
        embed.add_field(name="Song ID", value=_text(result.song.ref.id), inline=False)
        embed.add_field(name="Chart", value=_chart_text(result.chart), inline=False)
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _failure(self, ctx: PenguinContext):
        await ctx.reply(
            "Source unavailable or lookup invalid, missing or ambiguous. Check catalog sources and the exact song ID/difficulty/variant/chart ID. No source fallback was attempted.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ProviderCatalogCog(bot))
