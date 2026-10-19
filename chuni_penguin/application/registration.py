from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chuni_penguin.command_tree import PenguinCommandTree


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        ignored = {"id", "application_id", "guild_id", "version"}
        result = {}
        for key, item in value.items():
            if key in ignored or item is None:
                continue
            if key in {
                "options",
                "choices",
                "name_localizations",
                "description_localizations",
            } and item in ({}, []):
                continue
            if key in {"required", "autocomplete", "nsfw"} and item is False:
                continue
            if key == "default_member_permissions":
                item = str(item)
            if key in {"contexts", "integration_types"}:
                item = sorted(item)
            result[key] = _normalize(item)
        return result
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def registration_diff(
    local: Sequence[Mapping[str, Any]], remote: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    def index(payloads):
        return {(int(p.get("type", 1)), p["name"]): _normalize(p) for p in payloads}

    def label(key):
        return ("/" if key[0] == 1 else f"type{key[0]}:") + key[1]

    desired, existing = index(local), index(remote)
    return {
        "added": [label(k) for k in sorted(desired.keys() - existing.keys())],
        "removed": [label(k) for k in sorted(existing.keys() - desired.keys())],
        "changed": [
            label(k)
            for k in sorted(desired.keys() & existing.keys())
            if desired[k] != existing[k]
        ],
    }


async def preview_registration(tree: "PenguinCommandTree") -> dict[str, list[str]]:
    """Explicit owner request: read the remote global tree, never sync it."""
    commands = tree.get_commands()
    if (translator := tree.translator) is not None:
        local = [
            await command.get_translated_payload(tree, translator)
            for command in commands
        ]
    else:
        local = [command.to_dict(tree) for command in commands]
    remote = [command.to_dict() for command in await tree.fetch_commands()]
    return registration_diff(local, remote)
