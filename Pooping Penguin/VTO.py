import discord
from discord.ext import commands
import json
import os
import re
import asyncio
from datetime import timedelta
import random

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# File to store vote settings
SETTINGS_FILE = 'vote_settings.json'

# Store recent messages for repeat detection per channel
recent_messages = {}  # Dictionary with channel_id as key and list of messages as value

# Copypasta to send when "兒歌" is detected
COPYPASTA_BBSONG = """
喂！你話邊個CHUNITHM譜面係「兒歌」？

話晒CHUNITHM係音遊界嘅頂級機台，譜面設計精細到爆，點會係你講嗰啲低能兒歌可比！？係咪當你喺幼稚園拎住波波池嘅波玩咁簡單？

定係你連Basic難度都Clear唔到，仲喺度亂吠！屌你老母，係咪以為拎住兩粒Note亂笠就叫玩音遊啊？

仆你個街，CHUNITHM嘅譜面設計係為咗挑戰你嘅反應同節奏感，唔係畀你當卡拉OK機咁hea玩！你呢啲連筷子都拎唔穩嘅扑街，點會明白點樣Full Combo一首14+嘅歌！

講真，你咁樣亂講真係激嬲晒所有CHUNITHM玩家！有無試過喺機台前同班友連打幾粒鐘，汗流浹背仲要同機台嘅鬼畜判定鬥智鬥力？仲要望住個屏幕閃到好似癲病發作咁，條條Note同彩虹咁飛過嚟，你仲話係兒歌？！

有無試過為咗一個SSS Rank打到手指抽筋？仲喺度話「兒歌」，你係咪同CHUNITHM有仇啊？屌你嘅，你有種就去機鋪同我現場表演AJ一次「L9」嘅Ultima譜面！唔係就唔好喺度扮音遊達人，仲要亂講咁低能嘅評論！

我同你講，每一首歌、每一個譜面都係設計師同玩家嘅心血結晶！下次再喺度亂講「兒歌」，小心我叫Sum哥喺機鋪度同你單挑，用「祈 -我ら神祖と共に歩む者なり-」嘅Master譜面教你點樣做人！仆街，識講就講啲有建設性嘅嘢，唔係就收皮啦！
"""

COPYPASTA_XEVEL = """
我是一個剛上彩的玩家，今天是一個星期六，上了五天學終於可以去機廳打中二了。我投幣上 lõ機，聽到有首很好聽的歌 — Xevel
MASTER 14+ 是我平常根本不會觸碰的難度，但是這首歌真的很好聽，那就越一次級吧
我開始打，發現我對這首歌的譜面異常的熟練，彷彿好像已經打了很多次似的。遊戲結束 出來的成績是 1009106 SSS+ FC
不可能吧？我被這成績嚇到了 。我拿起手機，從倒影中發現自己的頭竟然長了一雙兔耳、頭髮變成白色、身上的T恤短褲和拖鞋還變成了地雷裙和皮鞋
我連忙想要跑出機廳，可是一切已經太晚了，我一邊跑，體型一邊縮小，皮膚一邊變白。最後我變成了一隻嬌小的、身體由棉花糖組成的兔子
"""

COPYPASTA_7381 = """
那一天的七三😭八一起來🥲那一天的七四🥹五六起來😰連同著迷🥺這個我操🔥破譜🥵萬般滋味👄那個你🫵都化作了鳥吋在我心底❤揮之不去檸檬 (垃圾譜師) 🍋的譜面💭在廁所🌞七九三五☔之前都無法被抹去😭如同嚼下甲口檸檬 (垃圾譜師) 🍋那樣讓我難忘記😭你是我永遠駐足眺望🫡的唯一那束光🔆
"""

COPYPASTA_WHOFINGER = """
【WHO finger】招生公告
     近日，本團出現了大量躺贏狗，上個月+今個月的共計pt為，3.0k。
因此，本團將會清理門戶並招攬熱血，對猩猩歌、手指歌、綜合歌（不包括盟月）有熱誠的團員。無論你的性別、年齡、膚色、國籍、手指數量、體重、身高、染色體數量、智商、手套厚薄、rating如何，我們都無任歡迎你的加入。
  福利：本團會定期設置team course，不定期舉辦線下網聚（不是銀趴），不定期派發染色體，團長會請團員飲隨機飲料，團長會有唐氏表演等
注意：*坐標香港優先*
（以下附上團長的艷照）
很帥，認同請分享
"""

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
    if any(keyword in current_message for keyword in ["兒歌", "老師", "teacher", "sensei", "眠", "nemu", "眠夢", "眼", "眼老", "nemumi", "umi", "oceanic", "cinaeco", "海洋", "xevel", "wup", "what's up? pop!", "1007381", "7381", "我操破譜", "臥槽破譜", "woc破譜", "whats up pop", "toilet", "tiola", "1007456", "7456", "厠所", "who finger", "誰手指", "世界衛生組織手指"]):
        print(f"Detected keyword in message: '{current_message}' from {message.author} in channel {message.channel.id}")
        try:
            # Send appropriate copypasta based on detected keyword
            if "兒歌" in current_message:
                await message.channel.send(COPYPASTA_BBSONG)
                print(f"Bot sent COPYPASTA_BBSONG in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in ["wup", "what's ? pop!", "1007381", "7381", "我操破譜", "臥槽破譜", "woc破譜", "whats up pop", "toilet", "tiola", "1007456", "7456", "厠所"]):
                await message.channel.send(COPYPASTA_7381)
                print(f"Bot sent COPYPASTA_7381 in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in ["who finger", "誰手指", "世界衛生組織手指"]):
                await message.channel.send(COPYPASTA_WHOFINGER)
                print(f"Bot sent COPYPASTA_7381 in channel {message.channel.id}")
            else:
                await message.channel.send(COPYPASTA_XEVEL)
                print(f"Bot sent COPYPASTA_XEVEL in channel {message.channel.id}")
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
