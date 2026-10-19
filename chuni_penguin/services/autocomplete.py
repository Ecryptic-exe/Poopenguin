"""Suggest catalog titles without depending on Discord or a reloadable Cog."""

from typing import TYPE_CHECKING

from rapidfuzz import fuzz, process

from chuni_penguin.constants import SIMILARITY_THRESHOLD

if TYPE_CHECKING:
    from chuni_penguin.services.catalog import AliasService


class SongAutocompleteService:
    def __init__(self, aliases: "AliasService"):
        self.aliases = aliases

    def suggest_titles(self, current: str, guild_id: int | None) -> list[str]:
        if len(current) < 3:
            return []

        aliases = self.aliases.alias_cache[0].copy()

        if (
            guild_id is not None
            and (guild_aliases := self.aliases.alias_cache.get(guild_id)) is not None
        ):
            aliases.extend(guild_aliases)

        results = process.extract(
            current,
            [x.alias for x in aliases],
            scorer=fuzz.QRatio,
            limit=50,
            score_cutoff=SIMILARITY_THRESHOLD,
        )
        titles = {aliases[r[2]].title for r in results}

        return list(titles)[:25]
