"""Bounded official Bot API/Gateway validation; no messages or command writes."""

import asyncio
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import aiohttp
import discord
import httpx
from discord.gateway import DiscordWebSocket
from yarl import URL

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config, use_config


class CheckBoundaryError(Exception):
    """The check attempted something outside its read-only boundary."""


def allowed_request(method, url, application_id, guild_id=None):
    target = URL(url)
    if method != "GET":
        return False
    if target.scheme == "wss":
        return target.host == "gateway.discord.gg" and target.path == "/"
    if target.scheme != "https" or target.host != "discord.com":
        return False
    paths = {
        "/api/v10/users/@me",
        "/api/v10/oauth2/applications/@me",
        "/api/v10/gateway/bot",
        "/api/v10/users/@me/guilds",
    }
    if application_id:
        paths.add(f"/api/v10/applications/{application_id}/commands")
        if guild_id:
            paths.add(
                f"/api/v10/applications/{application_id}/guilds/{guild_id}/commands"
            )
    return target.path in paths


async def run_discord_check(token, *, gateway=False, guild_id=None, timeout=35):
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "authenticated": False,
        "gateway_requested": gateway,
        "gateway_ready": False,
        "production_db_opened": False,
        "command_writes": 0,
        "message_sends": 0,
        "blocked_requests": 0,
        "blocked_gateway_operations": 0,
        "http_get_count": 0,
        "complete": False,
    }
    initial_tasks = set(asyncio.all_tasks())
    original_request = aiohttp.ClientSession._request
    original_gateway_send = DiscordWebSocket.send_as_json
    sessions = set()
    application_id = None

    async def guard_request(session, method, url, *args, **kwargs):
        if not allowed_request(method, url, application_id, guild_id):
            result["blocked_requests"] += 1
            raise CheckBoundaryError("request_outside_check_boundary")
        result["http_get_count"] += 1
        sessions.add(session)
        # Never forward a secret to a redirect destination.
        kwargs["allow_redirects"] = False
        return await original_request(session, method, url, *args, **kwargs)

    async def block_httpx(*args, **kwargs):
        result["blocked_requests"] += 1
        raise CheckBoundaryError("external_source_not_part_of_discord_check")

    async def block_send(*args, **kwargs):
        result["message_sends"] += 1
        raise CheckBoundaryError("message_send_prohibited")

    async def block_sync(*args, **kwargs):
        result["command_writes"] += 1
        raise CheckBoundaryError("command_sync_prohibited")

    async def guard_gateway_send(ws, payload):
        # IDENTIFY/RESUME/HEARTBEAT only; no presence or member requests.
        if payload.get("op") not in (1, 2, 6):
            result["blocked_gateway_operations"] += 1
            raise CheckBoundaryError("gateway_operation_prohibited")
        if payload.get("op") == 2:
            payload["d"]["presence"] = {
                "status": "invisible",
                "since": 0,
                "afk": False,
                "activities": [],
            }
        return await original_gateway_send(ws, payload)

    def ignore_event(*args, **kwargs):
        # Do not execute callbacks or react to real users during an isolated check.
        return None

    with TemporaryDirectory(prefix="penguin-discord-check-") as directory:
        path = Path(directory)
        database = path / "fixture.sqlite3"
        settings = Config.from_mapping(
            {
                "bot": {
                    "db_connection_string": await create_fixture_database(database),
                }
            }
        )
        with use_config(settings):
            from chuni_penguin.bot import ChuniBot
            from chuni_penguin.command_tree import PenguinCommandTree

            with (
                patch.object(aiohttp.ClientSession, "_request", guard_request),
                patch.object(httpx.AsyncClient, "send", block_httpx),
                patch.object(discord.abc.Messageable, "send", block_send),
                patch.object(PenguinCommandTree, "sync", block_sync),
                patch.object(PenguinCommandTree, "_from_interaction", ignore_event),
                patch.object(DiscordWebSocket, "send_as_json", guard_gateway_send),
            ):
                bot = ChuniBot(settings, offline=True, cache_dir=path / "cache")
                connection_task = None
                ready_task = None
                try:
                    async with bot:
                        await asyncio.wait_for(bot.login(token), timeout)
                        application_id = bot.application_id
                        result["authenticated"] = bool(bot.user and bot.user.bot)
                        result["startup"] = bot.startup_report.as_dict()
                        local_names = {
                            command.name for command in bot.tree.get_commands()
                        }
                        registered = await asyncio.wait_for(
                            bot.http.get_global_commands(application_id), 15
                        )
                        result["global_command_count"] = len(registered)
                        result["global_missing_local_commands"] = sorted(
                            local_names - {c["name"] for c in registered}
                        )
                        guilds = await asyncio.wait_for(
                            bot.http.get_guilds(limit=200), 15
                        )
                        result["guild_count"] = len(guilds)
                        result["test_guild_configured"] = guild_id is not None
                        if guild_id is None and len(guilds) == 1:
                            # Read commands in the sole existing guild without assuming
                            # it is a test server or changing any of its settings.
                            guild_id = int(guilds[0]["id"])
                            result["inspected_guild_selection"] = (
                                "sole_existing_guild_read_only"
                            )
                        if guild_id:
                            result["inspected_guild_accessible"] = any(
                                int(g["id"]) == guild_id for g in guilds
                            )
                            registered = await asyncio.wait_for(
                                bot.http.get_guild_commands(application_id, guild_id),
                                15,
                            )
                            result["guild_command_count"] = len(registered)
                            result["guild_missing_local_commands"] = sorted(
                                local_names - {c["name"] for c in registered}
                            )
                        if gateway:
                            with patch.object(bot, "dispatch", ignore_event):
                                connection_task = asyncio.create_task(
                                    bot.connect(reconnect=False),
                                    name="bounded-discord-gateway",
                                )
                                ready_task = asyncio.create_task(
                                    bot.wait_until_ready(), name="bounded-discord-ready"
                                )
                                done, _ = await asyncio.wait(
                                    {connection_task, ready_task},
                                    timeout=timeout,
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                if connection_task in done:
                                    await connection_task
                                if ready_task not in done:
                                    raise TimeoutError
                                await ready_task
                                result["gateway_ready"] = bot.is_ready()
                                result["gateway_shards"] = len(bot.shards)
                except discord.LoginFailure:
                    result["failure"] = "invalid_bot_token"
                except discord.PrivilegedIntentsRequired:
                    result["failure"] = (
                        "message_content_intent_requires_application_setting"
                    )
                except (TimeoutError, aiohttp.ClientError, discord.HTTPException):
                    result["failure"] = "discord_network_or_access_failure"
                except Exception as exc:
                    result["failure"] = "setup_or_check_failure"
                    result["failure_type"] = type(exc).__name__
                finally:
                    await bot.close()
                    for task in (connection_task, ready_task):
                        if task is not None:
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await task
                    result["client_closed"] = bot.is_closed()
                    result["cache_http_closed"] = bot.caching_http_client.is_closed
                    result["discord_http_closed"] = all(s.closed for s in sessions)
        with contextlib.closing(sqlite3.connect(database)) as conn:
            result["fixture_integrity_ok"] = conn.execute(
                "PRAGMA integrity_check"
            ).fetchall() == [("ok",)]
            result["fixture_foreign_keys_ok"] = (
                conn.execute("PRAGMA foreign_key_check").fetchall() == []
            )
    await asyncio.sleep(0)
    result["pending_task_count"] = sum(
        t not in initial_tasks and not t.done() for t in asyncio.all_tasks()
    )
    result["complete"] = bool(
        result["authenticated"]
        and result.get("startup", {}).get("complete")
        and result.get("startup", {}).get("database_ready")
        and (not gateway or result["gateway_ready"])
        and all(
            result.get(key)
            for key in (
                "client_closed",
                "cache_http_closed",
                "discord_http_closed",
                "fixture_integrity_ok",
                "fixture_foreign_keys_ok",
            )
        )
        and not any(
            result[key]
            for key in (
                "command_writes",
                "message_sends",
                "blocked_requests",
                "blocked_gateway_operations",
                "pending_task_count",
            )
        )
        and "failure" not in result
    )
    return result


async def check_sources(values):
    """Read only the explicit owner's Chunirec token, retaining no player data."""
    from chuni_penguin.services.player_sources import PlayerSourceService, SourceError

    token = values.get("CHUNIREC_PLAYER_TOKEN")
    if not token:
        return {
            "state": "not_configured",
            "required": "CHUNIREC_PLAYER_TOKEN",
            "data_saved": False,
        }
    region = values.get("CHUNIREC_REGION") or "jp2"
    if region not in ("jp", "jp2"):
        return {"state": "invalid_region", "data_saved": False}
    try:
        payload = await PlayerSourceService(None)._fetch(token, region)
    except SourceError:
        return {"state": "source_read_failed", "region": region, "data_saved": False}
    return {
        "state": "verified",
        "region": region,
        "record_count": len(payload["records"]),
        "data_saved": False,
    }


def suppress_logs():
    # Discord IDENTIFY and external request debug messages can contain secrets.
    logging.disable(logging.CRITICAL)
