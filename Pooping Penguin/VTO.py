import discord
from discord.ext import commands
import json
import os
import re
import asyncio
from datetime import timedelta
import random
from copypasta import *

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# File to store vote settings
SETTINGS_FILE = 'vote_settings.json'

# Store recent messages for repeat detection per channel
recent_messages = {}  # Dictionary with channel_id as key and list of messages as value


# Load vote settings
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {'required_votes': 3, 'admin_only': False}


# Save vote settings
def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)


# Parse time string (e.g., '1d', '2h', '30m', '10s', 'random')
def parse_time(time_str):
    if not time_str:  # Default to 5 minutes if no time is provided
        return timedelta(minutes=5)
    if time_str.lower() == 'random':  # Custom random duration
        rand = random.random() * 100  # Generate a random number between 0 and 100
        if rand < 6.9:  # 6.9% chance for 1 second
            return timedelta(seconds=1)
        elif rand < 75.9:  # 69% chance for 1 second to 1 week
            return timedelta(seconds=random.randint(1, 7 * 24 * 60 * 60))  # 1s to 604800s
        else:  # 24.1% chance for 1 week to 90 days
            return timedelta(seconds=random.randint(7 * 24 * 60 * 60, 90 * 24 * 60 * 60))  # 604800s to 7776000s
    match = re.match(r'^(\d+)([dhms])$', time_str)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'd':
        return timedelta(days=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'm':
        return timedelta(minutes=value)
    elif unit == 's':
        return timedelta(seconds=value)
    return None


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')


@bot.event
async def on_message(message):
    if message.author.bot:  # Ignore bot messages
        return

    # Process commands
    await bot.process_commands(message)

    # Check for specific keywords in the message
    current_message = message.content.strip().lower()
    if any(keyword in current_message for keyword in
           ["兒歌", "老師", "teacher", "sensei", "眠", "nemu", "眠夢", "眼", "眼老", "ねむ", "nemumi", "umi", "oceanic", "ocean",
            "cinaeco", "海洋", "xevel", "x7124", "wup", "what's up? pop!", "1007381", "7381", "我操破譜", "臥槽破譜", "woc破譜",
            "whats up pop", "toilet", "tiola", "厠所", "who finger", "誰手指", "世界衛生組織手指", "0識", "teach", "希望你教"]):
        print(f"Detected keyword in message: '{current_message}' from {message.author} in channel {message.channel.id}")
        try:
            # Send appropriate copypasta based on detected keyword
            if "兒歌" in current_message:
                await message.channel.send(COPYPASTA_BBSONG)
                print(f"Bot sent COPYPASTA_BBSONG in channel {message.channel.id}")

            elif any(keyword in current_message for keyword in
                     ["wup", "what's ? pop!", "1007381", "7381", "我操破譜", "臥槽破譜", "woc破譜", "whats up pop", "toilet",
                      "tiola", "1007456", "7456", "厠所"]):
                await message.channel.send(COPYPASTA_7381)
                print(f"Bot sent COPYPASTA_7381 in channel {message.channel.id}")

            elif any(keyword in current_message for keyword in ["rebellion"]):
                await message.channel.send(COPYPASTA_REBELLION)
                print(f"Bot sent COPYPASTA_REBELLION in channel {message.channel.id}")

            elif any(keyword in current_message for keyword in ["who finger", "誰手指", "世界衛生組織手指"]):
                await message.channel.send(COPYPASTA_WHOFINGER)
                print(f"Bot sent COPYPASTA_WHOFINGER in channel {message.channel.id}")

            elif any(keyword in current_message for keyword in ["0識", "teach", "希望你教"]):
                await message.channel.send(COPYPASTA_0KNOW)
                print(f"Bot sent COPYPASTA_0KNOW in channel {message.channel.id}")

            else:
                # Randomly select one of the three copypastas
                selected_copypasta = random.choice(
                    [COPYPASTA_XEVEL, COPYPASTA_X7124, COPYPASTA_MARSHMELLOWRABBIT1, COPYPASTA_MARSHMELLOWRABBIT2,
                     COPYPASTA_MARSHMELLOWRABBIT3, COPYPASTA_MARSHMELLOWRABBIT4, COPYPASTA_MARSHMELLOWRABBIT5,
                     COPYPASTA_MARSHMELLOWRABBIT6, COPYPASTA_MARSHMELLOWRABBIT7, COPYPASTA_MARSHMELLOWRABBIT8,
                     COPYPASTA_MARSHMELLOWRABBIT9, COPYPASTA_MARSHMELLOWRABBIT10, COPYPASTA_INSTANTNOODLES])
                await message.channel.send(selected_copypasta)
                print(
                    f"Bot sent randomly selected copypasta in channel {message.channel.id}: {selected_copypasta[:30]}...")

        except discord.errors.Forbidden:
            print(f"Failed to send copypasta in channel {message.channel.id}: Missing permissions")

    # Check for repeated messages in the specific channel
    channel_id = message.channel.id
    if current_message:  # Only process non-empty messages
        print(f"Processing message in channel {channel_id}: '{current_message}' from {message.author}")  # Debug log
        # Initialize channel message list if it doesn't exist
        if channel_id not in recent_messages:
            recent_messages[channel_id] = []

        # Add message to recent messages for this channel
        recent_messages[channel_id].append({'content': message.content.strip(), 'author': message.author.id})

        # Keep only the last 3 messages for this channel
        recent_messages[channel_id] = recent_messages[channel_id][-3:]

        # Debug: Print current state of recent_messages for this channel
        print(f"Recent messages in channel {channel_id}: {recent_messages[channel_id]}")

        # Check if we have 3 messages in this channel
        if len(recent_messages[channel_id]) == 3:
            # Check if all 3 messages are identical and from different users
            if (recent_messages[channel_id][0]['content'].lower() ==
                    recent_messages[channel_id][1]['content'].lower() ==
                    recent_messages[channel_id][2]['content'].lower() and
                    len(set(msg['author'] for msg in recent_messages[channel_id])) == 3):
                print(f"Three identical messages detected in channel {channel_id}: '{current_message}'")  # Debug log
                try:
                    await message.channel.send(current_message)
                    print(f"Bot sent in channel {channel_id}: '{current_message}'")  # Debug log
                    # Clear recent messages for this channel to prevent multiple triggers
                    recent_messages[channel_id] = []
                except discord.errors.Forbidden:
                    print(f"Failed to send message in channel {channel_id}: Missing permissions")  # Debug log


@bot.command()
async def vto(ctx, member: discord.Member, time_str: str = None):
    settings = load_settings()
    required_votes = settings['required_votes']
    admin_only = settings['admin_only']

    # Parse the time string
    timeout_duration = parse_time(time_str)
    if not timeout_duration:
        await ctx.send(
            "Invalid time format. Use format like `1d`, `2h`, `30m`, `10s`, or `random`. Default is 5m if omitted.")
        return

    # Check if bot has permission to timeout
    if not ctx.guild.me.guild_permissions.moderate_members:
        await ctx.send("I don't have permission to timeout members!")
        return

    # Check if random was specified
    is_random = time_str and time_str.lower() == 'random'

    # Create vote message
    vote_message = await ctx.send(
        f"Vote to timeout {member.mention} for {'random duration' if is_random else str(timeout_duration)}. "
        f"React with 🖕 to vote 'Yes'. "
        f"{'(Admin votes only)' if admin_only else f'({required_votes} votes needed)'}"
    )
    await vote_message.add_reaction('🖕')

    # Store vote data
    vote_data = {
        'message_id': vote_message.id,
        'target': member.id,
        'required_votes': required_votes,
        'admin_only': admin_only,
        'duration': timeout_duration.total_seconds(),
        'voters': []
    }

    # Save vote data to file
    with open('votes.json', 'w') as f:
        json.dump({str(vote_data['message_id']): vote_data}, f, indent=4)

    # Wait for 3 minutes to check if vote threshold is met
    await asyncio.sleep(180)  # 3 minutes
    with open('votes.json', 'r') as f:
        votes = json.load(f)
    if str(vote_data['message_id']) in votes:
        vote_data = votes[str(vote_data['message_id'])]
        if len(vote_data['voters']) < vote_data['required_votes']:
            await ctx.send(f"Not enough votes to timeout {member.mention}. Vote session closed.")
            del votes[str(vote_data['message_id'])]
            with open('votes.json', 'w') as f:
                json.dump(votes, f, indent=4)


@bot.command()
@commands.has_permissions(administrator=True)
async def setvote(ctx, arg: str):
    settings = load_settings()

    if arg.lower() == 'admin':
        settings['admin_only'] = True
        save_settings(settings)
        await ctx.send("Vote mode set to admin-only.")
    else:
        try:
            num_votes = int(arg)
            if num_votes < 1:
                await ctx.send("Number of votes must be at least 1.")
                return
            settings['required_votes'] = num_votes
            settings['admin_only'] = False
            save_settings(settings)
            await ctx.send(f"Required votes set to {num_votes}.")
        except ValueError:
            await ctx.send("Invalid input. Use a number (e.g., `5`) or `admin` for admin-only voting.")


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    # Load vote data
    if not os.path.exists('votes.json'):
        return
    with open('votes.json', 'r') as f:
        votes = json.load(f)

    message_id = str(reaction.message.id)
    if message_id not in votes:
        return

    vote_data = votes[message_id]
    if reaction.emoji != '🖕':
        return

    # Check if user is allowed to vote
    if vote_data['admin_only']:
        member = reaction.message.guild.get_member(user.id)
        if not member.guild_permissions.administrator:
            await reaction.remove(user)
            return

    # Add voter to list if not already voted
    if user.id not in vote_data['voters']:
        vote_data['voters'].append(user.id)

        # Check if vote threshold is reached
        if len(vote_data['voters']) >= vote_data['required_votes']:
            target = reaction.message.guild.get_member(vote_data['target'])
            if target:
                try:
                    duration = timedelta(seconds=vote_data['duration'])
                    await target.timeout(duration, reason="Voted out by community")
                    await reaction.message.channel.send(f"{target.mention} has been timed out for {duration}.")
                except discord.Forbidden:
                    await reaction.message.channel.send("Failed to timeout user. Missing permissions.")
            # Clean up vote data
            del votes[message_id]
        else:
            # Update vote data
            votes[message_id] = vote_data

        # Save updated vote data
        with open('votes.json', 'w') as f:
            json.dump(votes, f, indent=4)


bot.run('API')
