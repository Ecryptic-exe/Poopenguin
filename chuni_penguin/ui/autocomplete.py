from typing import TYPE_CHECKING

from discord import app_commands

if TYPE_CHECKING:
    from chuni_penguin.services.autocomplete import SongAutocompleteService


def song_title_choices(
    service: "SongAutocompleteService | None", current: str, guild_id: int | None
) -> list[app_commands.Choice[str]]:
    if service is None:
        return []
    return [
        app_commands.Choice(name=title, value=title)
        for title in service.suggest_titles(current, guild_id)
    ]
