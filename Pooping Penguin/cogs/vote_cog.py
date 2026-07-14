"""
Timeout voting: !vto/​/vto starts a vote, users react with the vote
emoji, on_reaction_add tallies votes and applies the timeout once
threshold is reached. !setvote/​/setvote configures how many votes are
required (or restricts voting to admins only).

vto and setvote are now commands.hybrid_command, so "!vto @user 30m"
and "/vto member:@user time_str:30m" run the exact same function.
"""
import asyncio
import re
from datetime import timedelta

import discord
from discord.ext import commands

from config import load_settings, save_settings, load_votes, save_votes
from i18n import t, get_guild_language

VOTE_EMOJI = "🖕"
VOTE_WINDOW_SECONDS = 180  # 3 minutes


def parse_time(time_str):
    """Parses '1d' / '2h' / '30m' / '10s' / 'random' into a timedelta.
    Returns None if the string doesn't match any known format."""
    if not time_str:
        return timedelta(minutes=5)
        if time_str.lower() == "random":
        import random
        rand = random.random() * 100
        if rand < 6.9:
            return timedelta(seconds=random.randint(7 * 24 * 60 * 60, 90 * 24 * 60 * 60))
        elif rand < 75.9:
            return timedelta(seconds=random.randint(1, 1 * 24 * 60 * 60))
        else:
            return timedelta(seconds=1)
    match = re.match(r"^(\d+)([dhms])$", time_str)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return {
        "d": timedelta(days=value),
        "h": timedelta(hours=value),
        "m": timedelta(minutes=value),
        "s": timedelta(seconds=value),
    }[unit]


class VoteCog(commands.Cog, name="vote"):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="vto",
        description="Initiates a vote to timeout a member from the server.")
    @discord.app_commands.describe(
        member="The member to vote on timing out",
        time_str="Duration: e.g. 1d, 2h, 30m, 10s, or 'random' (default 5m)")
    async def vto(self, ctx, member: discord.Member, time_str: str = None):
        """Initiates a vote to timeout a member from the server."""
        settings = load_settings()
        required_votes = settings["required_votes"]
        admin_only = settings["admin_only"]
        language = get_guild_language(settings, ctx.guild.id)

        timeout_duration = parse_time(time_str)
        if not timeout_duration:
            await ctx.send(t(language,
                "Invalid time format. Use format like `1d`, `2h`, `30m`, `10s`, or `random`. Default is 5m if omitted.",
                "無效的時間格式。請使用如 `1d`、`2h`、`30m`、`10s` 或 `random` 的格式。如果省略，默認為 5 分鐘。"))
            return

        if not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send(t(language,
                "I don't have permission to timeout members!",
                "我沒有權限暫停成員！"))
            return

        is_random = time_str and time_str.lower() == "random"
        duration_text = "random duration" if is_random else str(timeout_duration)
        duration_text_cn = "隨機時長" if is_random else str(timeout_duration)
        threshold_text = "(Admin votes only)" if admin_only else f"({required_votes} votes needed)"
        threshold_text_cn = "（僅限管理員投票）" if admin_only else f"（需要 {required_votes} 票）"

        vote_message = await ctx.send(t(language,
            f"Vote to timeout {member.mention} for {duration_text}. React with {VOTE_EMOJI} to vote 'Yes'. {threshold_text}",
            f"投票暫停 {member.mention} {duration_text_cn}。 使用 {VOTE_EMOJI} 反應投票 '是'。 {threshold_text_cn}"))
        # ctx.send() always returns a real discord.Message, whether this
        # command was invoked with "!" or "/", so add_reaction works the
        # same way either way.
        await vote_message.add_reaction(VOTE_EMOJI)

        vote_data = {
            "message_id": vote_message.id,
            "target": member.id,
            "required_votes": required_votes,
            "admin_only": admin_only,
            "duration": timeout_duration.total_seconds(),
            "voters": [],
            "channel_id": ctx.channel.id,
        }
        votes = load_votes()
        votes[str(vote_data["message_id"])] = vote_data
        save_votes(votes)

        await asyncio.sleep(VOTE_WINDOW_SECONDS)
        votes = load_votes()
        key = str(vote_data["message_id"])
        if key in votes:
            vote_data = votes[key]
            if len(vote_data["voters"]) < vote_data["required_votes"]:
                try:
                    channel = ctx.guild.get_channel(int(vote_data["channel_id"]))
                    if channel:
                        await channel.send(t(language,
                            f"Not enough votes to timeout {member.mention}. Vote session closed.",
                            f"沒有足夠的票數來暫停 {member.mention}。投票已關閉。"))
                except discord.Forbidden:
                    print(f"Failed to send vote closure message in channel {vote_data['channel_id']}: Missing permissions")
                del votes[key]
                save_votes(votes)

    @commands.hybrid_command(
        name="setvote",
        description="Configures the timeout voting system (Admin only).")
    @discord.app_commands.describe(arg="A number of required votes (e.g. 5), or 'admin' for admin-only voting")
    @commands.has_permissions(administrator=True)
    async def setvote(self, ctx, arg: str):
        """Configures the timeout voting system (Admin only)."""
        settings = load_settings()
        language = get_guild_language(settings, ctx.guild.id)

        if arg.lower() == "admin":
            settings["admin_only"] = True
            save_settings(settings)
            await ctx.send(t(language, "Vote mode set to admin-only.", "投票模式設置為僅限管理員。"))
            return

        try:
            num_votes = int(arg)
        except ValueError:
            await ctx.send(t(language,
                "Invalid input. Use a number (e.g., `5`) or `admin` for admin-only voting.",
                "無效輸入。請使用數字（例如，`5`）或 `admin` 進行僅限管理員投票。"))
            return

        if num_votes < 1:
            await ctx.send(t(language, "Number of votes must be at least 1.", "票數必須至少為 1。"))
            return

        settings["required_votes"] = num_votes
        settings["admin_only"] = False
        save_settings(settings)
        await ctx.send(t(language, f"Required votes set to {num_votes}.", f"所需票數設置為 {num_votes}。"))

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        if str(reaction.emoji) != VOTE_EMOJI:
            return

        votes = load_votes()
        message_id = str(reaction.message.id)
        if message_id not in votes:
            return
        vote_data = votes[message_id]

        if vote_data["admin_only"]:
            member = reaction.message.guild.get_member(user.id)
            if not member or not member.guild_permissions.administrator:
                await reaction.remove(user)
                return

        if user.id in vote_data["voters"]:
            return

        vote_data["voters"].append(user.id)
        votes[message_id] = vote_data
        save_votes(votes)

        if len(vote_data["voters"]) < vote_data["required_votes"]:
            return

        target = reaction.message.guild.get_member(vote_data["target"])
        language = get_guild_language(load_settings(), reaction.message.guild.id)
        if target:
            try:
                duration = timedelta(seconds=vote_data["duration"])
                await target.timeout(duration, reason="Voted to timeout")
                await reaction.message.channel.send(t(language,
                    f"{target.mention} has been timed out for {duration}.",
                    f"{target.mention} 已被暫停 {duration}。"))
            except discord.Forbidden:
                await reaction.message.channel.send(t(language,
                    "I don't have permission to timeout this member!",
                    "我沒有權限暫停此成員！"))
            except Exception as e:
                await reaction.message.channel.send(t(language,
                    f"An error occurred: {str(e)}",
                    f"發生錯誤：{str(e)}"))

        del votes[message_id]
        save_votes(votes)


async def setup(bot):
    await bot.add_cog(VoteCog(bot))
