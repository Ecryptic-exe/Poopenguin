"""Reaction-based timeout voting shared by prefix and slash commands."""

import asyncio
import contextlib
import logging
import random
import re
import time
from datetime import timedelta

import discord
from discord.ext import commands

from .config import load_settings, load_votes, save_settings, save_votes
from .i18n import get_guild_language, t

VOTE_EMOJI = "🖕"
VOTE_WINDOW_SECONDS = 180
MAX_TIMEOUT = timedelta(days=28)
_logger = logging.getLogger(__name__)


def parse_time(time_str):
    """Return a positive Discord timeout duration, or None for invalid input."""
    if not time_str:
        return timedelta(minutes=5)
    time_str = time_str.strip().lower()
    if time_str == "random":
        roll = random.random() * 100
        if roll < 6.9:
            return timedelta(seconds=random.randint(7 * 86400, 28 * 86400))
        if roll < 75.9:
            return timedelta(seconds=random.randint(1, 86400))
        if roll < 98.9:
            return timedelta(seconds=random.randint(86400, 28 * 86400))
        return timedelta(seconds=1)
    match = re.fullmatch(r"(\d+)([dhms])", time_str)
    if match is None:
        return None
    try:
        seconds = int(match[1]) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[match[2]]
        duration = timedelta(seconds=seconds)
    except (ValueError, OverflowError):
        return None
    return duration if timedelta(0) < duration <= MAX_TIMEOUT else None


class VoteCog(commands.Cog, name="vote"):
    def __init__(self, bot):
        self.bot = bot
        self._votes = {}
        self._vote_lock = asyncio.Lock()
        self._expiry_tasks = {}

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage
        return True

    async def cog_load(self):
        stored = await asyncio.to_thread(load_votes)
        now = time.time()
        # Legacy records have no deadline/guild; they cannot safely be resumed.
        self._votes = {
            key: vote
            for key, vote in stored.items()
            if vote.get("expires_at", 0) > now and vote.get("guild_id")
        }
        if self._votes != stored:
            await asyncio.to_thread(save_votes, self._votes)
        for key, vote in self._votes.items():
            self._schedule_expiry(key, vote["expires_at"])

    async def cog_unload(self):
        tasks = list(self._expiry_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._expiry_tasks.clear()

    def _schedule_expiry(self, key, expires_at):
        task = asyncio.create_task(
            self._expire_vote(key, expires_at), name=f"vote:{key}"
        )
        self._expiry_tasks[key] = task
        task.add_done_callback(lambda done: self._expiry_done(key, done))

    def _expiry_done(self, key, task):
        if self._expiry_tasks.get(key) is task:
            del self._expiry_tasks[key]
        if not task.cancelled():
            try:
                task.result()
            except Exception:
                _logger.exception("Failed to close vote %s", key)

    def _cancel_expiry(self, key):
        task = self._expiry_tasks.pop(key, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _save_votes(self):
        # Keep the state lock until a disk write really finishes, including when
        # an expiry task is cancelled during unload. to_thread cannot stop a
        # running worker, and releasing early would allow overlapping writes.
        writer = asyncio.create_task(asyncio.to_thread(save_votes, self._votes))
        try:
            await asyncio.shield(writer)
        except asyncio.CancelledError:
            await writer
            raise

    async def _pop_vote(self, key):
        async with self._vote_lock:
            vote = self._votes.pop(key, None)
            if vote is not None:
                await self._save_votes()
        if vote is not None:
            self._cancel_expiry(key)
        return vote

    async def _send_result(self, vote, english, chinese):
        channel = self.bot.get_channel(vote["channel_id"])
        try:
            if channel is None:
                channel = await self.bot.fetch_channel(vote["channel_id"])
            settings = await asyncio.to_thread(load_settings)
            language = get_guild_language(settings, vote["guild_id"])
            await channel.send(t(language, english, chinese))
        except discord.HTTPException:
            _logger.warning(
                "Could not announce vote result in channel %s", vote["channel_id"]
            )

    async def _expire_vote(self, key, expires_at):
        await asyncio.sleep(max(0, expires_at - time.time()))
        vote = await self._pop_vote(key)
        if vote is not None:
            mention = f"<@{vote['target']}>"
            await self._send_result(
                vote,
                f"Not enough votes to timeout {mention}. Vote session closed.",
                f"沒有足夠的票數來暫停 {mention}。投票已關閉。",
            )

    @commands.hybrid_command(
        name="vto", description="Initiates a vote to timeout a member from the server."
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        member="The member to vote on timing out",
        time_str="Duration: 1d, 2h, 30m, 10s, or 'random' (default 5m; maximum 28d)",
    )
    async def vto(self, ctx, member: discord.Member, time_str: str | None = None):
        """Initiate a three-minute vote without holding the command open."""
        await ctx.defer()
        settings = await asyncio.to_thread(load_settings)
        required_votes = settings["required_votes"]
        admin_only = settings["admin_only"]
        language = get_guild_language(settings, ctx.guild.id)
        duration = parse_time(time_str)
        if duration is None:
            await ctx.send(
                t(
                    language,
                    "Invalid duration. Use 1d, 2h, 30m, 10s, or random (greater than 0, up to 28 days). Default is 5m.",
                    "無效的時長。請使用 1d、2h、30m、10s 或 random（大於 0，最長 28 天）。預設為 5 分鐘。",
                )
            )
            return
        if not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send(
                t(
                    language,
                    "I don't have permission to timeout members!",
                    "我沒有權限暫停成員！",
                )
            )
            return

        is_random = time_str and time_str.strip().lower() == "random"
        duration_text = "random duration" if is_random else str(duration)
        duration_text_cn = "隨機時長" if is_random else str(duration)
        threshold = (
            f"({required_votes} admin votes needed)"
            if admin_only
            else f"({required_votes} votes needed)"
        )
        threshold_cn = (
            f"（需要 {required_votes} 票管理員投票）"
            if admin_only
            else f"（需要 {required_votes} 票）"
        )
        message = await ctx.send(
            t(
                language,
                f"Vote to timeout {member.mention} for {duration_text}. React with {VOTE_EMOJI} to vote 'Yes'. {threshold}",
                f"投票暫停 {member.mention} {duration_text_cn}。使用 {VOTE_EMOJI} 反應投票 '是'。{threshold_cn}",
            )
        )
        key = str(message.id)
        vote = {
            "message_id": message.id,
            "target": member.id,
            "required_votes": required_votes,
            "admin_only": admin_only,
            "duration": duration.total_seconds(),
            "voters": [],
            "channel_id": ctx.channel.id,
            "guild_id": ctx.guild.id,
            "expires_at": time.time() + VOTE_WINDOW_SECONDS,
        }
        # Register before adding our reaction; users can react during that await.
        async with self._vote_lock:
            self._votes[key] = vote
            await self._save_votes()
        self._schedule_expiry(key, vote["expires_at"])
        try:
            await message.add_reaction(VOTE_EMOJI)
        except discord.HTTPException:
            await self._pop_vote(key)
            await ctx.send(
                t(
                    language,
                    "I couldn't add the vote reaction. Vote session closed.",
                    "無法新增投票反應。投票已關閉。",
                )
            )

    @commands.hybrid_command(
        name="setvote", description="Configures the timeout voting system (Admin only)."
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        arg="A number of required votes (e.g. 5), or 'admin' for admin-only voting"
    )
    @commands.has_permissions(administrator=True)
    async def setvote(self, ctx, arg: str):
        settings = await asyncio.to_thread(load_settings)
        language = get_guild_language(settings, ctx.guild.id)
        if arg.lower() == "admin":
            settings["admin_only"] = True
            await asyncio.to_thread(save_settings, settings)
            await ctx.send(
                t(
                    language,
                    "Vote mode set to admin-only.",
                    "投票模式設置為僅限管理員。",
                )
            )
            return
        try:
            num_votes = int(arg)
        except ValueError:
            await ctx.send(
                t(
                    language,
                    "Invalid input. Use a number (e.g., 5) or admin for admin-only voting.",
                    "無效輸入。請使用數字（例如，5）或 admin 進行僅限管理員投票。",
                )
            )
            return
        if num_votes < 1:
            await ctx.send(
                t(language, "Number of votes must be at least 1.", "票數必須至少為 1。")
            )
            return
        settings["required_votes"] = num_votes
        settings["admin_only"] = False
        await asyncio.to_thread(save_settings, settings)
        await ctx.send(
            t(
                language,
                f"Required votes set to {num_votes}.",
                f"所需票數設置為 {num_votes}。",
            )
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        key = str(payload.message_id)
        if payload.guild_id is None or str(payload.emoji) != VOTE_EMOJI:
            return
        vote = self._votes.get(key)
        if vote is None or vote["guild_id"] != payload.guild_id:
            return
        # Guild reaction-add payloads include the member even without member intent.
        member = payload.member
        if member is None or member.bot:
            return
        if vote["admin_only"] and not member.guild_permissions.administrator:
            channel = self.bot.get_channel(payload.channel_id)
            if channel is not None:
                with contextlib.suppress(discord.HTTPException):
                    await channel.get_partial_message(
                        payload.message_id
                    ).remove_reaction(payload.emoji, member)
            return

        async with self._vote_lock:
            vote = self._votes.get(key)
            if (
                vote is None
                or vote["expires_at"] <= time.time()
                or payload.user_id in vote["voters"]
            ):
                return
            vote["voters"].append(payload.user_id)
            reached_threshold = len(vote["voters"]) >= vote["required_votes"]
            if reached_threshold:
                # Claim once before HTTP; another event cannot repeat the timeout
                # or save an old snapshot over another active vote.
                del self._votes[key]
            await self._save_votes()
        if reached_threshold:
            self._cancel_expiry(key)
            await self._apply_timeout(vote)

    async def _apply_timeout(self, vote):
        guild = self.bot.get_guild(vote["guild_id"])
        if guild is None:
            return
        try:
            target = guild.get_member(vote["target"])
            if target is None:
                target = await guild.fetch_member(vote["target"])
            duration = timedelta(seconds=vote["duration"])
            await target.timeout(duration, reason="Voted to timeout")
        except discord.NotFound:
            await self._send_result(
                vote,
                "The target member has left the server. Vote session closed.",
                "目標成員已離開伺服器。投票已關閉。",
            )
        except discord.Forbidden:
            await self._send_result(
                vote,
                "I don't have permission to timeout this member!",
                "我沒有權限暫停此成員！",
            )
        except discord.HTTPException:
            await self._send_result(
                vote,
                "Discord couldn't apply the timeout. Vote session closed; please try again.",
                "Discord 無法暫停此成員。投票已關閉，請重試。",
            )
        else:
            await self._send_result(
                vote,
                f"{target.mention} has been timed out for {duration}.",
                f"{target.mention} 已被暫停 {duration}。",
            )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._remove_reactions(payload, user_id=payload.user_id)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload):
        await self._remove_reactions(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload):
        await self._remove_reactions(payload, all_emoji=True)

    async def _remove_reactions(self, payload, *, user_id=None, all_emoji=False):
        if not all_emoji and str(payload.emoji) != VOTE_EMOJI:
            return
        async with self._vote_lock:
            vote = self._votes.get(str(payload.message_id))
            if vote is None or vote["guild_id"] != payload.guild_id:
                return
            if user_id is None:
                vote["voters"].clear()
            elif user_id in vote["voters"]:
                vote["voters"].remove(user_id)
            else:
                return
            await self._save_votes()

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        await self._pop_vote(str(payload.message_id))

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        for message_id in payload.message_ids:
            await self._pop_vote(str(message_id))


async def setup(bot):
    await bot.add_cog(VoteCog(bot))
