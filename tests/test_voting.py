import asyncio
import copy
import json
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

from chuni_penguin.cogs.poopenguin import config, vote_cog


@pytest_asyncio.fixture
async def voting(monkeypatch, tmp_path):
    storage = tmp_path / "votes.json"
    monkeypatch.setattr(config, "VOTES_FILE", str(storage))
    monkeypatch.setattr(
        vote_cog,
        "load_settings",
        lambda: {
            "required_votes": 3,
            "admin_only": False,
            "language": {},
        },
    )
    target = SimpleNamespace(id=99, mention="<@99>", timeout=AsyncMock())
    guild = SimpleNamespace(
        id=33,
        me=SimpleNamespace(guild_permissions=SimpleNamespace(moderate_members=True)),
        get_member=Mock(return_value=None),
        fetch_member=AsyncMock(return_value=target),
    )
    channel = SimpleNamespace(id=456, send=AsyncMock(), get_partial_message=Mock())
    bot = SimpleNamespace(
        get_guild=Mock(return_value=guild),
        get_channel=Mock(return_value=channel),
        fetch_channel=AsyncMock(return_value=channel),
    )
    cog = vote_cog.VoteCog(bot)
    await cog.cog_load()
    ctx = SimpleNamespace(
        guild=guild, channel=channel, defer=AsyncMock(), send=AsyncMock()
    )
    fixture = SimpleNamespace(
        cog=cog,
        ctx=ctx,
        bot=bot,
        guild=guild,
        channel=channel,
        target=target,
        storage=storage,
    )
    try:
        yield fixture
    finally:
        await cog.cog_unload()


async def start_vote(voting, message_id=701, time_str="30m"):
    message = SimpleNamespace(id=message_id, add_reaction=AsyncMock())
    voting.ctx.send.return_value = message
    await asyncio.wait_for(
        vote_cog.VoteCog.vto.callback(voting.cog, voting.ctx, voting.target, time_str),
        timeout=2,
    )
    return message


def reaction(
    user_id, message_id=701, *, admin=False, bot=False, emoji=vote_cog.VOTE_EMOJI
):
    return SimpleNamespace(
        guild_id=33,
        channel_id=456,
        message_id=message_id,
        user_id=user_id,
        emoji=emoji,
        member=SimpleNamespace(
            id=user_id,
            bot=bot,
            guild_permissions=SimpleNamespace(administrator=admin),
        ),
    )


@pytest.mark.asyncio
async def test_withdrawn_vote_then_b_c_d_timeouts_once_without_message_or_member_cache(
    voting,
):
    await start_vote(voting)
    for user in (1, 2, 2):  # Repeated delivery must not add a second vote.
        await voting.cog.on_raw_reaction_add(reaction(user))
    await voting.cog.on_raw_reaction_remove(reaction(1))
    assert voting.cog._votes["701"]["voters"] == [2]
    await voting.cog.on_raw_reaction_add(reaction(3))
    voting.target.timeout.assert_not_awaited()
    await voting.cog.on_raw_reaction_add(reaction(4))
    await voting.cog.on_raw_reaction_add(reaction(5))
    voting.target.timeout.assert_awaited_once_with(
        timedelta(minutes=30), reason="Voted to timeout"
    )
    voting.guild.fetch_member.assert_awaited_once_with(99)
    assert json.loads(voting.storage.read_text()) == {}


@pytest.mark.asyncio
async def test_removed_voter_can_rejoin(voting):
    await start_vote(voting)
    await voting.cog.on_raw_reaction_add(reaction(1))
    await voting.cog.on_raw_reaction_remove(reaction(1))
    await voting.cog.on_raw_reaction_add(reaction(1))
    assert voting.cog._votes["701"]["voters"] == [1]


@pytest.mark.asyncio
async def test_simultaneous_threshold_events_do_not_repeat_or_overwrite_other_votes(
    voting,
):
    await start_vote(voting)
    for user in (1, 2):
        await voting.cog.on_raw_reaction_add(reaction(user))
    started = asyncio.Event()
    release = asyncio.Event()

    async def timeout(*args, **kwargs):
        started.set()
        await release.wait()

    voting.target.timeout.side_effect = timeout
    threshold = asyncio.create_task(voting.cog.on_raw_reaction_add(reaction(3)))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.gather(
            voting.cog.on_raw_reaction_add(reaction(4)),
            voting.cog.on_raw_reaction_add(reaction(5)),
            start_vote(voting, 702),
        )
    finally:
        release.set()
        await threshold
    voting.target.timeout.assert_awaited_once()
    assert set(json.loads(voting.storage.read_text())) == {"702"}
    assert set(voting.cog._votes) == {"702"}


@pytest.mark.asyncio
async def test_admin_votes_ignore_bots_other_emoji_and_non_admins(voting):
    await start_vote(voting)
    voting.cog._votes["701"]["admin_only"] = True
    partial = SimpleNamespace(remove_reaction=AsyncMock())
    voting.channel.get_partial_message.return_value = partial
    for payload in (
        reaction(1),
        reaction(2, admin=True, bot=True),
        reaction(3, admin=True, emoji="👍"),
    ):
        await voting.cog.on_raw_reaction_add(payload)
    assert voting.cog._votes["701"]["voters"] == []
    partial.remove_reaction.assert_awaited_once()
    for user in (4, 5, 6):
        await voting.cog.on_raw_reaction_add(reaction(user, admin=True))
    voting.target.timeout.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_all", [False, True])
async def test_clearing_reactions_resets_only_the_affected_vote(voting, clear_all):
    await start_vote(voting)
    await start_vote(voting, 702)
    await voting.cog.on_raw_reaction_add(reaction(1))
    await voting.cog.on_raw_reaction_add(reaction(2, 702))
    if clear_all:
        await voting.cog.on_raw_reaction_clear(reaction(0))
    else:
        await voting.cog.on_raw_reaction_clear_emoji(reaction(0))
    assert voting.cog._votes["701"]["voters"] == []
    assert voting.cog._votes["702"]["voters"] == [2]


@pytest.mark.asyncio
async def test_vote_expires_in_background_and_rejects_late_reactions(
    voting, monkeypatch
):
    monkeypatch.setattr(vote_cog, "VOTE_WINDOW_SECONDS", 0.05)
    await start_vote(voting)
    await voting.cog._expiry_tasks["701"]
    await voting.cog.on_raw_reaction_add(reaction(1))
    assert not voting.cog._votes
    assert json.loads(voting.storage.read_text()) == {}
    voting.target.timeout.assert_not_awaited()
    assert "Not enough votes" in voting.channel.send.call_args.args[0]


@pytest.mark.asyncio
async def test_start_registers_before_awaiting_seed_reaction(voting):
    message = SimpleNamespace(id=701)

    async def seed(emoji):
        await voting.cog.on_raw_reaction_add(reaction(1))

    message.add_reaction = seed
    voting.ctx.send.return_value = message
    await vote_cog.VoteCog.vto.callback(voting.cog, voting.ctx, voting.target)
    assert voting.cog._votes["701"]["voters"] == [1]


@pytest.mark.asyncio
async def test_cog_reload_restores_deadlines_and_discards_expired_or_legacy_votes(
    voting,
):
    await start_vote(voting)
    stored = copy.deepcopy(voting.cog._votes)
    stored["old"] = {"target": 99}
    stored["expired"] = {**stored["701"], "expires_at": time.time() - 1}
    await voting.cog.cog_unload()
    config.save_votes(stored)
    await voting.cog.cog_load()
    assert set(voting.cog._votes) == {"701"}
    assert set(voting.cog._expiry_tasks) == {"701"}
    assert set(json.loads(voting.storage.read_text())) == {"701"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_deleted_vote_message_cancels_expiry(voting, bulk):
    await start_vote(voting)
    if bulk:
        await voting.cog.on_raw_bulk_message_delete(SimpleNamespace(message_ids={701}))
    else:
        await voting.cog.on_raw_message_delete(SimpleNamespace(message_id=701))
    assert not voting.cog._votes
    assert not voting.cog._expiry_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [discord.Forbidden, discord.NotFound, discord.HTTPException]
)
async def test_failed_timeout_closes_vote_and_reports_reason(voting, error):
    await start_vote(voting)
    response = SimpleNamespace(status=403, reason="fixture")
    voting.target.timeout.side_effect = error(response, "fixture")
    for user in (1, 2, 3):
        await voting.cog.on_raw_reaction_add(reaction(user))
    assert not voting.cog._votes
    assert json.loads(voting.storage.read_text()) == {}
    assert "has been timed out" not in voting.channel.send.call_args.args[0]


@pytest.mark.parametrize(
    "raw", ["0s", "29d", "9999999999999999999999d", "invalid", "-1m"]
)
def test_invalid_or_out_of_range_duration_is_rejected(raw):
    assert vote_cog.parse_time(raw) is None


@pytest.mark.parametrize("roll", [0, 0.068, 0.069, 0.758, 0.759, 0.988, 0.989, 0.999])
def test_every_random_branch_stays_within_discord_timeout_limit(monkeypatch, roll):
    monkeypatch.setattr(vote_cog.random, "random", lambda: roll)
    monkeypatch.setattr(vote_cog.random, "randint", lambda low, high: high)
    assert timedelta(0) < vote_cog.parse_time("random") <= timedelta(days=28)


@pytest.mark.asyncio
async def test_cancelled_expiry_keeps_state_locked_until_disk_write_finishes(
    voting, monkeypatch
):
    await start_vote(voting)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    original_save = vote_cog.save_votes

    def save(value):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=2)
        original_save(value)

    monkeypatch.setattr(vote_cog, "save_votes", save)
    task = asyncio.create_task(voting.cog._pop_vote("701"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        await asyncio.sleep(0)
        assert voting.cog._vote_lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
