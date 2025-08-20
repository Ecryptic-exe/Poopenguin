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

# Remove default help command
bot.remove_command('help')

# File to store vote, language, and auto-reaction settings
SETTINGS_FILE = 'vote_settings.json'
VOTES_FILE = 'votes.json'

# Store recent messages for repeat detection per channel
recent_messages = {}  # Dictionary with channel_id as key and list of messages as value

# Status messages to rotate
STATUS_MESSAGES = [
    "Type !help or ping me for full command manual",
    "Listening to Frums - Parvorbital",
    "Listening to Ice - Floor Of Lava",
    "ね、簡単でしょ？",
    "Contact natherox on Discord for support"
]

# Load settings (vote, language, and auto-reaction)
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {'required_votes': 3, 'admin_only': False, 'language': {}, 'autoreact': {}}

# Save settings
def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

# Load votes
def load_votes():
    if os.path.exists(VOTES_FILE):
        with open(VOTES_FILE, 'r') as f:
            return json.load(f)
    return {}

# Save votes
def save_votes(votes):
    with open(VOTES_FILE, 'w') as f:
        json.dump(votes, f, indent=4)

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

# Function to rotate status messages
async def rotate_status():
    while True:
        for status in STATUS_MESSAGES:
            await bot.change_presence(activity=discord.Game(name=status))
            await asyncio.sleep(10)  # Wait 10 seconds before changing to next status

# Help menu with buttons
class HelpMenu(discord.ui.View):
    def __init__(self, ctx, command_list, language, timeout=180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.command_list = command_list
        self.language = language
        self.current_page = 0

    def get_embed(self):
        command = self.command_list[self.current_page]
        embed = discord.Embed(
            title=f"Command: !{command['name']}" if self.language == 'english' else f"命令：!{command['name']}",
            description=command['description'][self.language],
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🔹 Usage" if self.language == 'english' else "🔹 使用方法",
            value=command['usage'],
            inline=False
        )

        embed.add_field(
            name="🔹 Arguments" if self.language == 'english' else "🔹 參數",
            value=command['arguments'][self.language],
            inline=False
        )

        embed.add_field(
            name="🔹 Notes" if self.language == 'english' else "🔹 注意事項",
            value=command['notes'][self.language],
            inline=False
        )

        embed.set_footer(
            text=f"Page {self.current_page + 1}/{len(self.command_list)} | Use !help for the full user manual." if self.language == 'english' else
            f"第 {self.current_page + 1}/{len(self.command_list)} 頁 | 使用 !help 獲取完整的使用手冊。"
        )
        return embed

    @discord.ui.button(
        label="Previous" if load_settings().get('language', {}).get('default', 'english') == 'english' else "上一頁",
        style=discord.ButtonStyle.grey)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.current_page = (self.current_page - 1) % len(self.command_list)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(
        label="Next" if load_settings().get('language', {}).get('default', 'english') == 'english' else "下一頁",
        style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.current_page = (self.current_page + 1) % len(self.command_list)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(
        label="Close" if load_settings().get('language', {}).get('default', 'english') == 'english' else "關閉",
        style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        await interaction.response.edit_message(
            content="Help menu closed." if self.language == 'english' else "幫助選單已關閉。", embed=None, view=None)
        self.stop()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    # Start the status rotation
    bot.loop.create_task(rotate_status())

@bot.event
async def on_message(message):
    if message.author.bot:  # Ignore bot messages
        return

    # Auto-react to messages in channels with configured emoji
    settings = load_settings()
    channel_id = str(message.channel.id)
    if channel_id in settings.get('autoreact', {}):
        autoreact_settings = settings['autoreact'][channel_id]
        # Handle legacy format (string instead of dictionary)
        if isinstance(autoreact_settings, str):
            # Convert legacy string to new dictionary format
            settings['autoreact'][channel_id] = {
                'emoji': autoreact_settings,
                'user_id': None
            }
            save_settings(settings)  # Save updated settings
            autoreact_settings = settings['autoreact'][channel_id]  # Refresh variable
        # Now proceed with auto-reaction logic
        emoji = autoreact_settings['emoji']
        user_id = autoreact_settings.get('user_id')
        # Apply reaction if no specific user is set or if the message author matches the specified user
        if user_id is None or str(message.author.id) == user_id:
            try:
                await message.add_reaction(emoji)
                print(f"Auto-reacted with {emoji} to message in channel {channel_id}")
            except discord.Forbidden:
                print(f"Failed to auto-react in channel {channel_id}: Missing permissions")
            except discord.HTTPException:
                print(f"Failed to auto-react in channel {channel_id}: Invalid emoji")

    # Check if the bot is mentioned
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        content = message.content.strip().lower()
        command = content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
        ctx = await bot.get_context(message)
        # If the bot is mentioned with no command, 'poop penguin', or 'help', show the general help menu
        if not command or command in ['poop penguin', 'help']:
            await bot.get_command('help')(ctx)
        else:
            # Check if the command is valid and show specific command help
            valid_commands = ['vto', 'setvote', 'lang', 'ask', 'pick', 'rng', 'rcg', 'setperms', 'autoreact']
            if command in valid_commands:
                await bot.get_command('help')(ctx, command=command)
            else:
                await ctx.send(
                    f"No command named `{command}` found. Use `!help` to see all available commands." if load_settings().get(
                        'language', {}).get(str(ctx.guild.id), 'english') == 'english' else
                    f"未找到名為 `{command}` 的命令。使用 `!help` 查看所有可用命令。"
                )
        return

    # Process commands
    await bot.process_commands(message)

    # Check for specific keywords in the message
    current_message = message.content.strip().lower()
    if any(keyword in current_message for keyword in
           ["兒歌", "老師", "teacher", "sensei", "眠", "nemu", "眠夢", "眼老", "ねむ", "nemumi", "marshmellow rabbit",
            "oceanic", "angel dust", "cinaeco", "海洋", "xevel", "7124", "aca", "caca", "卡卡", "貓男",
            "wanderers", "wup", "what's up? pop!", "7381", "我操破譜", "臥槽破譜", "woc破譜", "whats up pop",
            "toilet", "tiola", "厠所", "who finger", "誰手指", "世界衛生組織手指", "rebellion", "0識",
            "希望你教", "希望教", "我我我", "me me me", "mememe", "私私私", "吾吾吾", "火龍果", "火龍威果", "pitaya", "dragon fruit",
            "giselle", "吉賽兒", "鷄飼料", "雞飼料", "son of sun", "sos", "太陽", "太陽之子", "太陽兒子", "日兒子",
            "loong9", "西龍九", "西鳳九", "西凰九", "西九小凰帝", "西九鳳凰小皇帝", "suika", "西瓜", "harry", "哈利陳", "狼鬼", "ウルガレオン"]):
        print(f"Detected keyword in message: '{current_message}' from {message.author} in channel {message.channel.id}")
        try:
            # Send appropriate copypasta based on detected keyword
            if "兒歌" in current_message:
                await message.channel.send(COPYPASTA_BBSONG)
                print(f"Bot sent COPYPASTA_BBSONG in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in
                     ["我我我", "me me me", "mememe", "私私私", "吾吾吾"]):
                await message.channel.send(COPYPASTA_MEMEME)
                print(f"Bot sent COPYPASTA_4MEMEME in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in
                     ["giselle", "吉賽兒", "鷄飼料", "雞飼料", "son of sun", "sos", "太陽之子", "太陽兒子", "日兒子", "太陽"]):
                await message.channel.send("狗也不屌")
                print(f"Bot sent 狗也不屌 in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in
                     ["火龍果", "火龍威果", "pitaya", "dragon fruit"]):
                await message.channel.send(COPYPASTA_4PITAYA)
                print(f"Bot sent COPYPASTA_4PITAYA in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in
                     ["wup", "what's up? pop!", "1007381", "7381", "我操破譜", "臥槽破譜", "woc破譜", "whats up pop", "toilet",
                      "tiola", "1007456", "7456", "厠所"]):
                await message.channel.send(COPYPASTA_7381)
                print(f"Bot sent COPYPASTA_7381 in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in ["rebellion"]):
                await message.channel.send(COPYPASTA_REBELLION)
                print(f"Bot sent COPYPASTA_REBELLION in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in ["who finger", "誰手指", "世界衛生組織手指"]):
                await message.channel.send(COPYPASTA_WHOFINGER)
                print(f"Bot sent COPYPASTA_WHOFINGER in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in ["0識", "希望你教", "希望教"]):
                await message.channel.send(COPYPASTA_0KNOW)
                print(f"Bot sent COPYPASTA_0KNOW in channel {message.channel.id}")
            elif any(keyword in current_message for keyword in
                     ["loong9", "西龍九", "西鳳九", "西凰九", "西九小凰帝", "西九鳳凰小皇帝"]):
                # Randomly select one of the copypastas
                selected_copypasta1 = random.choice(
                    [COPYPASTA_LOONG91, COPYPASTA_LOONG92, COPYPASTA_LOONG93, COPYPASTA_LOONG94, COPYPASTA_LOONG95,
                     COPYPASTA_LOONG96, COPYPASTA_LOONG97, COPYPASTA_LOONG98, COPYPASTA_LOONG99, COPYPASTA_LOONG910,
                     COPYPASTA_LOONG911, COPYPASTA_LOONG912])
                await message.channel.send(selected_copypasta1)
                print(f"Bot sent randomly selected copypasta in channel {message.channel.id}: {selected_copypasta1[:30]}...")
            elif any(keyword in current_message for keyword in ["harry", "哈利陳", "狼鬼", "ウルガレオン"]):
                # Randomly select one of the copypastas
                selected_copypasta2 = random.choice(
                    [COPYPASTA_HARRYCH1, COPYPASTA_HARRYCH2, COPYPASTA_LOONG99, COPYPASTA_HARRYCH3, COPYPASTA_HARRYCH4])
                await message.channel.send(selected_copypasta2)
                print(f"Bot sent randomly selected copypasta in channel {message.channel.id}: {selected_copypasta2[:30]}...")
            elif any(keyword in current_message for keyword in ["suika", "西瓜"]):
                await message.channel.send(COPYPASTA_SUIKA)
                print(f"Bot sent COPYPASTA_SUIKA in channel {message.channel.id}")
            else:
                # Randomly select one of the copypastas
                selected_copypasta = random.choice(
                    [COPYPASTA_XEVEL, COPYPASTA_X7124P1, COPYPASTA_X7124P2, COPYPASTA_X7124P3, COPYPASTA_X7124P4,
                     COPYPASTA_MARSHMELLOWRABBIT1, COPYPASTA_MARSHMELLOWRABBIT2,
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
        print(f"Processing message in channel {channel_id}: '{current_message}' from {message.author}")
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
                print(f"Three identical messages detected in channel {channel_id}: '{current_message}'")
                try:
                    await message.channel.send(current_message)
                    print(f"Bot sent in channel {channel_id}: '{current_message}'")
                    # Clear recent messages for this channel to prevent multiple triggers
                    recent_messages[channel_id] = []
                except discord.errors.Forbidden:
                    print(f"Failed to send message in channel {channel_id}: Missing permissions")

@bot.command()
async def help(ctx, *, command: str = None):
    """Displays the user manual for the bot or specific command details."""
    settings = load_settings()
    guild_id = str(ctx.guild.id)
    language = settings.get('language', {}).get(guild_id, 'english')

    # Define command list for the flippable menu
    command_list = [
        {
            'name': 'help',
            'description': {
                'english': "Displays the user manual for the bot or specific command details.",
                'chinese': "顯示機器人使用手冊或特定命令的詳細信息。"
            },
            'usage': "`!help [command]`",
            'arguments': {
                'english': "**command**: (Optional) The command to get detailed help for (e.g., `vto`).\n- Example: `!help vto`",
                'chinese': "**命令**：（可選）要獲取詳細幫助的命令（例如，`vto`）。\n- 示例：`!help vto`"
            },
            'notes': {
                'english': "- Shows a general user manual if no command is specified.\n- Use with a command name to see detailed help.\n- Supports button navigation for browsing commands.",
                'chinese': "- 如果未指定命令，顯示通用使用手冊。\n- 與命令名稱一起使用以查看詳細幫助。\n- 支持按鈕導航以瀏覽命令。"
            }
        },
        {
            'name': 'vto',
            'description': {
                'english': "Initiates a vote to timeout a member from the server.",
                'chinese': "發起投票以從伺服器暫停某成員。"
            },
            'usage': "`!vto <@member> [time]`",
            'arguments': {
                'english': "**member**: The user to timeout (must be mentioned, e.g., `@User`).\n**time_str**: (Optional) Duration of the timeout (e.g., `1d`, `2h`, `30m`, `10s`, or `random`). Defaults to 5 minutes if omitted.\n- Formats: `1d` (days), `2h` (hours), `30m` (minutes), `10s` (seconds), or `random` (random duration from 1 second to 90 days).\n- Example: `!vto @User 30m`, `!vto @User random`.",
                'chinese': "**成員**：要暫停的用戶（必須提及，例如，`@User`）。\n**時間**：（可選）暫停的持續時間（例如，`1d`、`2h`、`30m`、`10s` 或 `random`）。如果省略，默認為 5 分鐘。\n- 格式：`1d`（天）、`2h`（小時）、`30m`（分鐘）、`10s`（秒）或 `random`（1 秒到 90 天的隨機時長）。\n- 示例：`!vto @User 30m`、`!vto @User random`。"
            },
            'notes': {
                'english': "- Users vote by reacting with 🖕 to the vote message.\n- Voting lasts 3 minutes.\n- The bot requires `moderate_members` permission to timeout users.\n- Voting can be configured via `!setvote` to require a specific number of votes or be admin-only.\n- Multiple vote sessions can run concurrently.",
                'chinese': "- 用戶通過對投票消息反應 🖕 進行投票。\n- 投票持續 3 分鐘。\n- 機器人需要 `moderate_members` 權限來暫停用戶。\n- 投票可通過 `!setvote` 配置為需要特定票數或僅限管理員。\n- 可同時進行多個投票會話。"
            }
        },
        {
            'name': 'setvote',
            'description': {
                'english': "Configures the timeout voting system (Admin only).",
                'chinese': "配置暫停投票系統（僅限管理員）。"
            },
            'usage': "`!setvote <number | admin>`",
            'arguments': {
                'english': "**arg**: Either a number (e.g., `5`) to set the required number of votes, or `admin` to restrict voting to admins only.\n- Number must be at least 1.\n- Example: `!setvote 3` (sets 3 votes required), `!setvote admin` (admin-only voting).",
                'chinese': "**參數**：可以是一個數字（例如，`5`）來設置所需的票數，或 `admin` 以將投票限制為僅限管理員。\n- 數字必須至少為 1。\n- 示例：`!setvote 3`（設置需要 3 票），`!setvote admin`（僅限管理員投票）。"
            },
            'notes': {
                'english': "- Requires administrator permissions.\n- Changes are saved persistently in `vote_settings.json`.\n- Invalid inputs (e.g., non-numeric values other than `admin`) will result in an error message.",
                'chinese': "- 需要管理員權限。\n- 更改將持久保存到 `vote_settings.json`。\n- 無效輸入（例如，除 `admin` 外的非數字值）將導致錯誤消息。"
            }
        },
        {
            'name': 'lang',
            'description': {
                'english': "Toggles the language of the bot's help panel between English and Chinese.",
                'chinese': "切換機器人幫助面板的語言在英文和中文之間。"
            },
            'usage': "`!lang`",
            'arguments': {
                'english': "No arguments required.",
                'chinese': "無需參數。"
            },
            'notes': {
                'english': "- Toggles the language for the entire server.\n- Changes are saved persistently in `vote_settings.json`.\n- Affects only the `!help` command output.",
                'chinese': "- 為整個伺服器切換語言。\n- 更改將持久保存到 `vote_settings.json`。\n- 僅影響 `!help` 命令的輸出。"
            }
        },
        {
            'name': 'ask',
            'description': {
                'english': "Asks a question and receives a response based on a random success rate.",
                'chinese': "提出問題並根據隨機成功率獲得回應。"
            },
            'usage': "`!ask [question]`",
            'arguments': {
                'english': "**question**: The question to ask (e.g., `Will it rain today?`).\n- Example: `!ask Will I win the lottery?`",
                'chinese': "**問題**：要提出的問題（例如，`今天會下雨嗎？`）。\n- 示例：`!ask 我會中彩票嗎？`"
            },
            'notes': {
                'english': "- Responses are based on a random success rate (0-100%).\n- Higher success rates yield more positive responses; lower rates yield negative or uncertain responses.\n- The question is included in the response for context.",
                'chinese': "- 回應基於隨機成功率（0-100%）。\n- 較高的成功率會產生更積極的回應；較低的成功率會產生否定或不確定的回應。\n- 問題將包含在回應中以提供上下文。"
            }
        },
        {
            'name': 'pick',
            'description': {
                'english': "Randomly selects one option from a list of provided choices.",
                'chinese': "從提供的選項列表中隨機選擇一個。"
            },
            'usage': "`!pick [choice1] [choice2] [choice3]...`",
            'arguments': {
                'english': "**choices**: A list of options to choose from (at least one required).\n- Example: `!pick apple banana orange`",
                'chinese': "**選項**：要選擇的選項列表（至少需要一個）。\n- 示例：`!pick 蘋果 香蕉 橙子`"
            },
            'notes': {
                'english': "- At least one choice must be provided.\n- Choices are separated by spaces.\n- The bot will select one option randomly.",
                'chinese': "- 必須提供至少一個選項。\n- 選項之間用空格分隔。\n- 機器人將隨機選擇一個選項。"
            }
        },
        {
            'name': 'rng',
            'description': {
                'english': "Generates a random number between a specified minimum and maximum.",
                'chinese': "在指定的最小值和最大值之間生成一個隨機數。"
            },
            'usage': "`!rng [min] [max] [int/float]`",
            'arguments': {
                'english': "**min**: (Optional) The minimum value (defaults to 1).\n**max**: (Optional) The maximum value (defaults to 100).\n**type**: (Optional) `int` or `float` to specify the number type (defaults to `int`).\n- Example: `!rng 1 10 int`, `!rng 0.0 1.0 float`",
                'chinese': "**最小值**：（可選）最小值（默認為 1）。\n**最大值**：（可選）最大值（默認為 100）。\n**類型**：（可選）`int` 或 `float` 指定數字類型（默認為 `int`）。\n- 示例：`!rng 1 10 int`、`!rng 0.0 1.0 float`"
            },
            'notes': {
                'english': "- If type is not specified, integer is assumed.\n- Min and max must be valid numbers, and min must be less than or equal to max.\n- For floats, the result is rounded to 2 decimal places.",
                'chinese': "- 如果未指定類型，假設為整數。\n- 最小值和最大值必須是有效數字，且最小值必須小於或等於最大值。\n- 對於浮點數，結果四捨五入到小數點後兩位。"
            }
        },
        {
            'name': 'rcg',
            'description': {
                'english': "Generates a random color in hexadecimal format with a preview.",
                'chinese': "生成一個隨機的十六進制格式顏色並附帶預覽。"
            },
            'usage': "`!rcg`",
            'arguments': {
                'english': "No arguments required.\n- Example: `!rcg`",
                'chinese': "無需參數。\n- 示例：`!rcg`"
            },
            'notes': {
                'english': "- Returns a random color in hexadecimal format (e.g., #FF5733) with a preview in an embed.\n- The color is generated by randomly selecting values for red, green, and blue channels.",
                'chinese': "- 返回一個隨機的十六進制格式顏色（例如，#FF5733）並在嵌入中顯示預覽。\n- 顏色通過隨機選擇紅、綠、藍通道的值生成。"
            }
        },
        {
            'name': 'setperms',
            'description': {
                'english': "Grants permissions to a specific role in a specific channel (Admin only).",
                'chinese': "在特定頻道中為特定角色授予權限（僅限管理員）。"
            },
            'usage': "`!setperms <channel_id> <role_id>`",
            'arguments': {
                'english': "**channel_id**: The ID of the channel to modify permissions for.\n**role_id**: The ID of the role to grant permissions to.",
                'chinese': "**頻道 ID**：要修改權限的頻道 ID。\n**角色 ID**：要授予權限的角色 ID。"
            },
            'notes': {
                'english': "- Requires administrator permissions.\n- The bot must have `manage_channels` permission.\n- Grants view, send messages, and read message history permissions to the role.\n- Use Discord Developer Mode to get channel and role IDs.",
                'chinese': "- 需要管理員權限。\n- 機器人必須具有 `manage_channels` 權限。\n- 為角色授予查看、發送消息和閱讀消息歷史記錄的權限。\n- 使用 Discord 開發者模式獲取頻道和角色 ID。"
            }
        },
        {
            'name': 'autoreact',
            'description': {
                'english': "Sets an emoji to auto-react to messages from a specific user or all messages in the channel, or disables auto-reactions.",
                'chinese': "設置一個表情符號以自動對頻道中特定用戶或所有消息進行反應，或禁用自動反應。"
            },
            'usage': "`!autoreact [emoji] [user]`",
            'arguments': {
                'english': "**emoji**: (Optional) The emoji to auto-react with. If omitted, disables auto-reactions in the channel.\n**user**: (Optional) The user whose messages to auto-react to (must be mentioned, e.g., `@User`). If omitted, reacts to all messages.\n- Example: `!autoreact 😊 @User`, `!autoreact 😊`, `!autoreact` (disables auto-reactions)",
                'chinese': "**表情符號**：（可選）用於自動反應的表情符號。如果省略，則禁用頻道中的自動反應。\n**用戶**：（可選）要自動反應的用戶消息（必須提及，例如，`@User`）。如果省略，則對所有消息進行反應。\n- 示例：`!autoreact 😊 @User`、`!autoreact 😊`、`!autoreact`（禁用自動反應）"
            },
            'notes': {
                'english': "- No special permissions required for users.\n- The bot must have `add_reactions` permission.\n- Settings are saved persistently in `vote_settings.json`.\n- Only one emoji can be set per channel, and it applies to either a specific user or all messages.",
                'chinese': "- 用戶無需特殊權限。\n- 機器人必須具有 `add_reactions` 權限。\n- 設置將持久保存到 `vote_settings.json`。\n- 每個頻道只能設置一個表情符號，且適用於特定用戶或所有消息。"
            }
        }
    ]

    if not command:
        # General help with flippable menu
        embed = discord.Embed(
            title="Bot User Manual" if language == 'english' else "機器人使用手冊",
            description=(
                "Welcome to the bot! Use the buttons below to browse command details." if language == 'english' else
                "歡迎使用本機器人！使用下面的按鈕瀏覽命令詳細信息。"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🔹 Command Prefix" if language == 'english' else "🔹 命令前綴",
            value="Use `!` as the command prefix (e.g., `!help`)." if language == 'english' else
            "使用 `!` 作為命令前綴（例如，`!help`）。",
            inline=False
        )

        embed.add_field(
            name="🔹 Features" if language == 'english' else "🔹 功能",
            value=(
                "**Keyword Responses**: Sends copypastas for specific keywords in messages.\n"
                "**Repeat Detection**: Echoes a message if three different users send it consecutively.\n"
                "**Timeout Voting**: Use `!vto` to vote for timing out a member. Configurable via `!setvote`. Supports multiple votes at once."
                if language == 'english' else
                "**關鍵詞回應**：對消息中的特定關鍵詞回應迷因文本。\n"
                "**重複檢測**：若三個不同用戶連續發送相同消息，則重複該消息。\n"
                "**暫停投票**：使用 `!vto` 投票暫停成員。可通過 `!setvote` 配置。支持同時多個投票。"
            ),
            inline=False
        )

        embed.add_field(
            name=" " if language == 'english' else " ",
            value=(
                "**Random Response**: `!ask` gives answers based on a random success rate.\n"
                "**Random Choice**: `!pick` selects one option from a list.\n"
                "**Random Number**: `!rng` generates a number in a range.\n"
                "**Random Color**: `!rcg` creates a hex color with a preview.\n"
                "**Permissions**: `!setperms` grants channel access (admin only).\n"
                "**Auto-Reactions**: `!autoreact` sets emoji reactions for messages."
                if language == 'english' else
                "**隨機回應**：`!ask` 根據隨機成功率回應。\n"
                "**隨機選擇**：`!pick` 從選項列表中選一個。\n"
                "**隨機數**：`!rng` 生成範圍內的數字。\n"
                "**隨機顏色**：`!rcg` 生成十六進制顏色並預覽。\n"
                "**權限**：`!setperms` 授予頻道權限（僅限管理員）。\n"
                "**自動反應**：`!autoreact` 為消息設置表情反應。"
            ),
            inline=False
        )

        embed.add_field(
            name="🔹 Navigation" if language == 'english' else "🔹 導航",
            value=(
                "Use the buttons below to browse individual command details." if language == 'english' else
                "使用下面的按鈕瀏覽各個命令的詳細信息。"
            ),
            inline=False
        )

        embed.set_footer(
            text="Bot created for fun and moderation. Contact natherox through Discord for issues." if language == 'english' else
            "機器人為娛樂和管理而創建。如有問題，請於Discord聯繫natherox。"
        )
        view = HelpMenu(ctx, command_list, language)
        await ctx.send(embed=embed, view=view)

    else:
        # Specific command help
        command = command.lower()
        selected_command = next((cmd for cmd in command_list if cmd['name'] == command), None)
        if selected_command:
            embed = discord.Embed(
                title=f"Command: !{command}" if language == 'english' else f"命令：!{command}",
                description=selected_command['description'][language],
                color=discord.Color.blue()
            )

            embed.add_field(
                name="🔹 Usage" if language == 'english' else "🔹 使用方法",
                value=selected_command['usage'],
                inline=False
            )

            embed.add_field(
                name="🔹 Arguments" if language == 'english' else "🔹 參數",
                value=selected_command['arguments'][language],
                inline=False
            )

            embed.add_field(
                name="🔹 Notes" if language == 'english' else "🔹 注意事項",
                value=selected_command['notes'][language],
                inline=False
            )

            embed.set_footer(
                text="Use !help for the full user manual." if language == 'english' else
                "使用 !help 獲取完整的使用手冊。"
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="Error" if language == 'english' else "錯誤",
                description=f"No command named `{command}` found. Use `!help` to see all available commands." if language == 'english' else
                f"未找到名為 `{command}` 的命令。使用 `!help` 查看所有可用命令。",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

@bot.command()
async def vto(ctx, member: discord.Member, time_str: str = None):
    settings = load_settings()
    required_votes = settings['required_votes']
    admin_only = settings['admin_only']
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    # Parse the time string
    timeout_duration = parse_time(time_str)
    if not timeout_duration:
        await ctx.send(
            "Invalid time format. Use format like `1d`, `2h`, `30m`, `10s`, or `random`. Default is 5m if omitted." if language == 'english' else
            "無效的時間格式。請使用如 `1d`、`2h`、`30m`、`10s` 或 `random` 的格式。如果省略，默認為 5 分鐘。"
        )
        return

    # Check if bot has permission to timeout
    if not ctx.guild.me.guild_permissions.moderate_members:
        await ctx.send(
            "I don't have permission to timeout members!" if language == 'english' else
            "我沒有權限暫停成員！"
        )
        return

    # Check if random was specified
    is_random = time_str and time_str.lower() == 'random'

    # Create vote message
    vote_message = await ctx.send(
        f"Vote to timeout {member.mention} for {'random duration' if is_random else str(timeout_duration)}. "
        f"React with 🖕 to vote 'Yes'. "
        f"{'(Admin votes only)' if admin_only else f'({required_votes} votes needed)'}"
        if language == 'english' else
        f"投票暫停 {member.mention} {'隨機時長' if is_random else str(timeout_duration)}。 "
        f"使用 🖕 反應投票 '是'。 "
        f"{'（僅限管理員投票）' if admin_only else f'（需要 {required_votes} 票）'}"
    )
    await vote_message.add_reaction('🖕')

    # Store vote data
    vote_data = {
        'message_id': vote_message.id,
        'target': member.id,
        'required_votes': required_votes,
        'admin_only': admin_only,
        'duration': timeout_duration.total_seconds(),
        'voters': [],
        'channel_id': ctx.channel.id  # Store channel ID for cleanup messages
    }

    # Load existing votes and add new vote
    votes = load_votes()
    votes[str(vote_data['message_id'])] = vote_data
    save_votes(votes)

    # Wait for 3 minutes to check if vote threshold is met
    await asyncio.sleep(180)  # 3 minutes
    votes = load_votes()
    if str(vote_data['message_id']) in votes:
        vote_data = votes[str(vote_data['message_id'])]
        if len(vote_data['voters']) < vote_data['required_votes']:
            try:
                channel = ctx.guild.get_channel(int(vote_data['channel_id']))
                if channel:
                    await channel.send(
                        f"Not enough votes to timeout {member.mention}. Vote session closed." if language == 'english' else
                        f"沒有足夠的票數來暫停 {member.mention}。投票已關閉。"
                    )
            except discord.Forbidden:
                print(f"Failed to send vote closure message in channel {vote_data['channel_id']}: Missing permissions")
            del votes[str(vote_data['message_id'])]
            save_votes(votes)

@bot.command()
@commands.has_permissions(administrator=True)
async def setvote(ctx, arg: str):
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    if arg.lower() == 'admin':
        settings['admin_only'] = True
        save_settings(settings)
        await ctx.send(
            "Vote mode set to admin-only." if language == 'english' else
            "投票模式設置為僅限管理員。"
        )
    else:
        try:
            num_votes = int(arg)
            if num_votes < 1:
                await ctx.send(
                    "Number of votes must be at least 1." if language == 'english' else
                    "票數必須至少為 1。"
                )
                return
            settings['required_votes'] = num_votes
            settings['admin_only'] = False
            save_settings(settings)
            await ctx.send(
                f"Required votes set to {num_votes}." if language == 'english' else
                f"所需票數設置為 {num_votes}。"
            )
        except ValueError:
            await ctx.send(
                "Invalid input. Use a number (e.g., `5`) or `admin` for admin-only voting." if language == 'english' else
                "無效輸入。請使用數字（例如，`5`）或 `admin` 進行僅限管理員投票。"
            )

@bot.command()
async def lang(ctx):
    """Toggles the language of the bot's help panel between English and Chinese."""
    settings = load_settings()
    guild_id = str(ctx.guild.id)
    current_language = settings.get('language', {}).get(guild_id, 'english')
    new_language = 'chinese' if current_language == 'english' else 'english'
    settings['language'] = settings.get('language', {})
    settings['language'][guild_id] = new_language
    save_settings(settings)
    await ctx.send(f"Language set to {new_language.capitalize()}." if new_language == 'english' else f"語言設置為中文。")

@bot.command()
async def ask(ctx, *, question: str):
    """Asks a question and responds based on a random success rate."""
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    # Generate random success rate (0-100%)
    success_rate = random.random() * 100

    # Determine response based on success rate
    if language == 'english':
        if success_rate >= 80:
            response = f"Regarding '{question}', it looks very likely to succeed!"
        elif success_rate >= 50:
            response = f"For '{question}', there's a decent chance it could happen."
        elif success_rate >= 20:
            response = f"About '{question}', it's not very likely, but who knows?"
        else:
            response = f"Sorry, for '{question}', it seems quite unlikely."
    else:
        if success_rate >= 80:
            response = f"關於 '{question}'，看起來非常有可能成功！"
        elif success_rate >= 50:
            response = f"對於 '{question}'，有不錯的機會可能會發生。"
        elif success_rate >= 20:
            response = f"關於 '{question}'，不太可能，但誰知道呢？"
        else:
            response = f"抱歉，對於 '{question}'，看起來相當不可能。"

    await ctx.send(response)

@bot.command()
async def pick(ctx, *choices):
    """Randomly selects one option from the provided choices."""
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    if not choices:
        await ctx.send(
            "Please provide at least one choice." if language == 'english' else
            "請提供至少一個選項。"
        )
        return

    choice = random.choice(choices)
    await ctx.send(
        f"I picked: {choice}" if language == 'english' else
        f"我選擇了：{choice}"
    )

@bot.command()
async def rng(ctx, min_val: str = '1', max_val: str = '100', type: str = 'int'):
    """Generates a random number between min and max (default is integer)."""
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    # Validate number type
    if type.lower() not in ['int', 'float']:
        await ctx.send(
            "Type must be 'int' or 'float'." if language == 'english' else
            "類型必須是 'int' 或 'float'。"
        )
        return

    # Convert min and max to numbers
    try:
        min_num = float(min_val)
        max_num = float(max_val)
    except ValueError:
        await ctx.send(
            "Min and max must be valid numbers." if language == 'english' else
            "最小值和最大值必須是有效數字。"
        )
        return

    # Ensure min is less than or equal to max
    if min_num > max_num:
        await ctx.send(
            "Minimum value must be less than or equal to maximum value." if language == 'english' else
            "最小值必須小於或等於最大值。"
        )
        return

    # Generate random number
    if type.lower() == 'int':
        result = random.randint(int(min_num), int(max_num))
    else:
        result = round(random.uniform(min_num, max_num), 2)

    await ctx.send(
        f"Random number: {result}" if language == 'english' else
        f"隨機數：{result}"
    )

@bot.command()
async def rcg(ctx):
    """Generates a random color in hexadecimal format with a preview."""
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    # Generate random RGB values
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)

    # Convert to hexadecimal format
    color_hex = f"#{r:02x}{g:02x}{b:02x}".upper()

    # Create embed with color preview
    embed = discord.Embed(
        title="Random Color" if language == 'english' else "隨機顏色",
        description=f"Hex: {color_hex}",
        color=discord.Color.from_rgb(r, g, b)
    )
    embed.add_field(
        name="RGB" if language == 'english' else "RGB值",
        value=f"({r}, {g}, {b})",
        inline=True
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setperms(ctx, channel_id: str, role_id: str):
    """Grants permissions to a specific role in a specific channel (Admin only)."""
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')

    # Validate that inputs are numeric (IDs)
    try:
        channel_id = int(channel_id)
        role_id = int(role_id)
    except ValueError:
        await ctx.send(
            "Channel ID and Role ID must be valid numbers." if language == 'english' else
            "頻道 ID 和角色 ID 必須是有效數字。"
        )
        return

    # Fetch the channel and role
    channel = ctx.guild.get_channel_or_thread(channel_id)  # Supports both text channels and threads
    role = ctx.guild.get_role(role_id)

    # Check if channel and role exist
    if not channel:
        await ctx.send(
            "Channel not found. Please provide a valid channel ID." if language == 'english' else
            "找不到頻道。請提供有效的頻道 ID。"
        )
        return
    if not role:
        await ctx.send(
            "Role not found. Please provide a valid role ID." if language == 'english' else
            "找不到角色。請提供有效的角色 ID。"
        )
        return

    # Check if bot has manage_channels permission
    if not ctx.guild.me.guild_permissions.manage_channels:
        await ctx.send(
            "I don't have permission to manage channels!" if language == 'english' else
            "我沒有管理頻道的權限！"
        )
        return

    try:
        # Define the permissions to grant
        permissions = {
            role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        # Update the channel's permissions
        await channel.edit(overwrites={**channel.overwrites, **permissions})
        await ctx.send(
            f"Permissions updated: Role {role.mention} can now view and send messages in {channel.mention}." if language == 'english' else
            f"權限已更新：角色 {role.mention} 現在可以在 {channel.mention} 中查看和發送消息。"
        )
    except discord.Forbidden:
        await ctx.send(
            "Failed to update permissions. Missing permissions." if language == 'english' else
            "無法更新權限。缺少權限。"
        )
    except Exception as e:
        await ctx.send(
            f"An error occurred: {str(e)}" if language == 'english' else
            f"發生錯誤：{str(e)}"
        )

@bot.command()
async def autoreact(ctx, emoji: str = None, user: discord.Member = None):
    """Sets an emoji to auto-react to messages from a specific user or all messages in the channel, or disables auto-reactions."""
    settings = load_settings()
    language = settings.get('language', {}).get(str(ctx.guild.id), 'english')
    channel_id = str(ctx.channel.id)

    # Check if bot has add_reactions permission
    if not ctx.guild.me.guild_permissions.add_reactions:
        await ctx.send(
            "I don't have permission to add reactions!" if language == 'english' else
            "我沒有添加反應的權限！"
        )
        return

    # Initialize autoreact settings if not present
    settings['autoreact'] = settings.get('autoreact', {})

    if emoji:
        # Validate the emoji
        try:
            # Attempt to add the emoji as a reaction to verify it's valid
            temp_message = await ctx.send("Testing emoji...")
            await temp_message.add_reaction(emoji)
            await temp_message.delete()
        except discord.HTTPException:
            await ctx.send(
                "Invalid emoji. Please provide a valid emoji." if language == 'english' else
                "無效的表情符號。請提供有效的表情符號。"
            )
            return
        except discord.Forbidden:
            await ctx.send(
                "I don't have permission to add reactions!" if language == 'english' else
                "我沒有添加反應的權限！"
            )
            return

        # Set the emoji and optional user for auto-reactions in this channel
        settings['autoreact'][channel_id] = {
            'emoji': emoji,
            'user_id': str(user.id) if user else None
        }
        save_settings(settings)
        if user:
            await ctx.send(
                f"Auto-reactions enabled: Will react with {emoji} to messages from {user.mention} in {ctx.channel.mention}." if language == 'english' else
                f"自動反應已啟用：將對 {ctx.channel.mention} 中 {user.mention} 的消息使用 {emoji} 進行反應。"
            )
        else:
            await ctx.send(
                f"Auto-reactions enabled: Will react with {emoji} to all messages in {ctx.channel.mention}." if language == 'english' else
                f"自動反應已啟用：將對 {ctx.channel.mention} 中的所有消息使用 {emoji} 進行反應。"
            )
    else:
        # Disable auto-reactions for this channel
        if channel_id in settings['autoreact']:
            del settings['autoreact'][channel_id]
            save_settings(settings)
            await ctx.send(
                f"Auto-reactions disabled in {ctx.channel.mention}." if language == 'english' else
                f"已在 {ctx.channel.mention} 中禁用自動反應。"
            )
        else:
            await ctx.send(
                f"Auto-reactions were not enabled in {ctx.channel.mention}." if language == 'english' else
                f"{ctx.channel.mention} 中尚未啟用自動反應。"
            )

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    # Load vote data
    if not os.path.exists(VOTES_FILE):
        return
    votes = load_votes()

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
        votes[message_id] = vote_data  # Update the votes dictionary
        save_votes(votes)

        # Check if vote threshold is reached
        if len(vote_data['voters']) >= vote_data['required_votes']:
            target = reaction.message.guild.get_member(vote_data['target'])
            language = load_settings().get('language', {}).get(str(reaction.message.guild.id), 'english')
            if target:
                try:
                    duration = timedelta(seconds=vote_data['duration'])
                    await target.timeout(duration, reason="Voted to timeout")
                    await reaction.message.channel.send(
                        f"{target.mention} has been timed out for {duration}." if language == 'english' else
                        f"{target.mention} 已被暫停 {duration}。"
                    )
                except discord.Forbidden:
                    await reaction.message.channel.send(
                        "I don't have permission to timeout this member!" if language == 'english' else
                        "我沒有權限暫停此成員！"
                    )
                except Exception as e:
                    await reaction.message.channel.send(
                        f"An error occurred: {str(e)}" if language == 'english' else
                        f"發生錯誤：{str(e)}"
                    )
            del votes[message_id]
            save_votes(votes)

bot.run('API')
