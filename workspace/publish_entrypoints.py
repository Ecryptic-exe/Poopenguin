"""Publish the user-requested reduced surface only to existing command scopes."""

import argparse
import asyncio
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

from chuni_penguin.application.credentials import (
    read_env_file,
    settings_from_env,
    token_from_env,
)
from chuni_penguin.application.offline import create_fixture_database, deny_network
from chuni_penguin.bot import ChuniBot
from chuni_penguin.config import Config, use_config

ROOT = Path(__file__).resolve().parents[1]
B_ROOTS = {"ask", "pick", "rng", "rcg", "gacha", "copypasta", "keyword"}


def command_shape(command):
    """Compare functional schemas while accepting Discord metadata normalization."""

    def normalize(value):
        if isinstance(value, dict):
            return {
                k: sorted(v) if k == "channel_types" else normalize(v)
                for k, v in value.items()
                if v is not None and v is not False and v != [] and v != {}
            }
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value

    keys = (
        ("name", "description", "options") if command.get("type", 1) == 1 else ("name",)
    )
    return normalize({k: command[k] for k in keys if k in command})


async def main(*, apply=False):
    logging.getLogger("chuni_penguin").setLevel(logging.ERROR)
    logging.getLogger("discord").setLevel(logging.ERROR)
    prior = json.loads(
        (ROOT / "data/runtime/command-scopes-before.json").read_text(encoding="utf-8")
    )
    values = read_env_file(ROOT / ".env")
    with tempfile.TemporaryDirectory(prefix="penguin-entrypoints-") as directory:
        url = await create_fixture_database(Path(directory) / "fixture.sqlite3")
        settings = settings_from_env(
            Config.from_mapping({"bot": {"db_connection_string": url}}), values
        )
        attempts = []
        with use_config(settings), deny_network(attempts):
            async with ChuniBot(
                settings, offline=True, cache_dir=Path(directory) / "cache"
            ) as bot:
                await bot.setup_hook()
                commands = [c.to_dict(bot.tree) for c in bot.tree.get_commands()]
        assert not attempts
    roots = {c["name"] for c in commands if c.get("type", 1) == 1}
    assert (
        roots
        == {"help", "account", "chart", "score", "tools", "compare", "permissions"}
        | B_ROOTS
    )
    # All original Poopenguin commands/subcommands and their schemas must survive.
    old_b = {
        c["name"]: c for c in prior["scopes"][0]["commands"] if c["name"] in B_ROOTS
    }
    new_b = {c["name"]: c for c in commands if c["name"] in B_ROOTS}

    shape = command_shape

    diffs = [name for name in B_ROOTS if shape(old_b[name]) != shape(new_b[name])]
    if diffs:
        (ROOT / "reports/poopenguin-schema-diff.json").write_text(
            json.dumps(
                {
                    name: {"old": shape(old_b[name]), "new": shape(new_b[name])}
                    for name in diffs
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Poopenguin schema differences:", ", ".join(sorted(diffs)))
    assert {k: shape(v) for k, v in old_b.items()} == {
        k: shape(v) for k, v in new_b.items()
    }
    (ROOT / "reports/command-schema-proposed.json").write_text(
        json.dumps(commands, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not apply:
        print(
            json.dumps(
                {
                    "proposed_roots": sorted(roots),
                    "context_menus": [
                        c["name"] for c in commands if c.get("type", 1) != 1
                    ],
                    "poopenguin_schema_unchanged": True,
                    "remote_writes": 0,
                },
                ensure_ascii=False,
            )
        )
        return
    token = token_from_env(values)
    completed = []
    async with httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        headers={"Authorization": "Bot " + token},
        timeout=25,
    ) as client:
        # Read and validate every existing scope before any registration writes.
        # Preserve current registrations in a separate backup, not the historical baseline.
        current_scopes = []
        for scope in prior["scopes"]:
            suffix = (
                "/commands"
                if scope["guild_id"] is None
                else f"/guilds/{scope['guild_id']}/commands"
            )
            endpoint = f"/applications/{prior['application_id']}" + suffix
            response = await client.get(endpoint)
            response.raise_for_status()
            current = response.json()
            current_scopes.append(
                {
                    "guild_id": scope["guild_id"],
                    "commands": current,
                    "endpoint": endpoint,
                }
            )
        backup = (
            ROOT
            / "data/runtime"
            / (
                "command-scopes-before-select-"
                + datetime.now().strftime("%Y%m%d-%H%M%S")
                + ".json"
            )
        )
        backup.write_text(
            json.dumps(
                {
                    "application_id": prior["application_id"],
                    "scopes": [
                        {k: v for k, v in scope.items() if k != "endpoint"}
                        for scope in current_scopes
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        proposed = {(c["name"], c.get("type", 1)): c for c in commands}
        for scope in current_scopes:
            current = scope["commands"]
            if not current:
                continue
            for existing in current:
                key = (existing["name"], existing.get("type", 1))
                if key not in proposed:
                    raise RuntimeError(
                        "Unexpected current command; refusing to remove it: "
                        + existing["name"]
                    )
                if existing["name"] not in {"chart", "score"} and shape(
                    existing
                ) != shape(proposed[key]):
                    raise RuntimeError(
                        "Unexpected schema difference; refusing to overwrite: "
                        + existing["name"]
                    )
        for scope in current_scopes:
            current = scope["commands"]
            if not current:
                continue
            existing_by_key = {(c["name"], c.get("type", 1)): c for c in current}
            payload = []
            for command in commands:
                existing = existing_by_key.get(
                    (command["name"], command.get("type", 1)), {}
                )
                preserved = {
                    k: existing[k]
                    for k in (
                        "default_member_permissions",
                        "dm_permission",
                        "integration_types",
                        "contexts",
                        "nsfw",
                    )
                    if k in existing
                }
                payload.append(command | preserved)
            response = await client.put(scope["endpoint"], json=payload)
            response.raise_for_status()
            verify = await client.get(scope["endpoint"])
            verify.raise_for_status()
            actual = verify.json()
            assert {(c["name"], c.get("type", 1)) for c in actual} == {
                (c["name"], c.get("type", 1)) for c in commands
            }
            assert {(c["name"], c.get("type", 1)): shape(c) for c in actual} == {
                key: shape(c) for key, c in proposed.items()
            }
            completed.append(
                {
                    "scope": "global"
                    if scope["guild_id"] is None
                    else "existing_guild",
                    "before_chat_input_roots": len(
                        [c for c in scope["commands"] if c.get("type", 1) == 1]
                    ),
                    "after_chat_input_roots": len(roots),
                    "context_menus": [c["name"] for c in actual if c.get("type") != 1],
                    "verified": True,
                }
            )
    report = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "authorization": "User requested /chart and /score entry points and retention of all Poopenguin functionality.",
        "registered_scopes": completed,
        "chat_input_roots": sorted(roots),
        "poopenguin_schema_unchanged": True,
        "private_credentials_logged": False,
        "backup": "ignored " + str(backup.relative_to(ROOT)),
    }
    (ROOT / "reports/command-registration-20261019.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(apply=parser.parse_args().apply))
