"""
!help - shows either a flippable-page manual (no argument) or details
for one specific command.

This is a straight port of the HelpMenu/help() code that used to sit
at the bottom of vto.py, with one real bug fix: the Previous/Next/Close
button labels used to be decided once, at class-definition time, from
whatever the *default* guild's language happened to be - so every
server's help menu showed buttons in the same language regardless of
their own !lang setting. Labels are now set per-instance in __init__
based on the language passed in, which is what the rest of the code
already assumed was happening.
"""
import discord
from discord.ext import commands

from config import load_settings
from i18n import t, get_guild_language

# (name, description_en, description_zh, usage, args_en, args_zh, notes_en, notes_zh)
COMMAND_LIST = [
    {
        "name": "help",
        "description": {"english": "Displays the user manual for the bot or specific command details.",
                         "chinese": "顯示機器人使用手冊或特定命令的詳細信息。"},
        "usage": "`!help [command]`",
        "arguments": {"english": "**command**: (Optional) The command to get detailed help for (e.g., `vto`).\n- Example: `!help vto`",
                      "chinese": "**命令**：（可選）要獲取詳細幫助的命令（例如，`vto`）。\n- 示例：`!help vto`"},
        "notes": {"english": "- Shows a general user manual if no command is specified.\n- Use with a command name to see detailed help.\n- Supports button navigation for browsing commands.",
                  "chinese": "- 如果未指定命令，顯示通用使用手冊。\n- 與命令名稱一起使用以查看詳細幫助。\n- 支持按鈕導航以瀏覽命令。"}
    },
    {
        "name": "vto",
        "description": {"english": "Initiates a vote to timeout a member from the server.",
                         "chinese": "發起投票以從伺服器暫停某成員。"},
        "usage": "`!vto <@member> [time]`",
        "arguments": {"english": "**member**: The user to timeout (must be mentioned, e.g., `@User`).\n**time_str**: (Optional) Duration of the timeout (e.g., `1d`, `2h`, `30m`, `10s`, or `random`). Defaults to 5 minutes if omitted.\n- Formats: `1d` (days), `2h` (hours), `30m` (minutes), `10s` (seconds), or `random` (random duration from 1 second to 90 days).\n- Example: `!vto @User 30m`, `!vto @User random`.",
                      "chinese": "**成員**：要暫停的用戶（必須提及，例如，`@User`）。\n**時間**：（可選）暫停的持續時間（例如，`1d`、`2h`、`30m`、`10s` 或 `random`）。如果省略，默認為 5 分鐘。\n- 格式：`1d`（天）、`2h`（小時）、`30m`（分鐘）、`10s`（秒）或 `random`（1 秒到 90 天的隨機時長）。\n- 示例：`!vto @User 30m`、`!vto @User random`。"},
        "notes": {"english": "- Users vote by reacting with 🖕 to the vote message.\n- Voting lasts 3 minutes.\n- The bot requires `moderate_members` permission to timeout users.\n- Voting can be configured via `!setvote` to require a specific number of votes or be admin-only.\n- Multiple vote sessions can run concurrently.",
                  "chinese": "- 用戶通過對投票消息反應 🖕 進行投票。\n- 投票持續 3 分鐘。\n- 機器人需要 `moderate_members` 權限來暫停用戶。\n- 投票可通過 `!setvote` 配置為需要特定票數或僅限管理員。\n- 可同時進行多個投票會話。"}
    },
    {
        "name": "setvote",
        "description": {"english": "Configures the timeout voting system (Admin only).",
                         "chinese": "配置暫停投票系統（僅限管理員）。"},
        "usage": "`!setvote <number | admin>`",
        "arguments": {"english": "**arg**: Either a number (e.g., `5`) to set the required number of votes, or `admin` to restrict voting to admins only.\n- Number must be at least 1.\n- Example: `!setvote 3` (sets 3 votes required), `!setvote admin` (admin-only voting).",
                      "chinese": "**參數**：可以是一個數字（例如，`5`）來設置所需的票數，或 `admin` 以將投票限制為僅限管理員。\n- 數字必須至少為 1。\n- 示例：`!setvote 3`（設置需要 3 票），`!setvote admin`（僅限管理員投票）。"},
        "notes": {"english": "- Requires administrator permissions.\n- Changes are saved persistently in `vote_settings.json`.\n- Invalid inputs (e.g., non-numeric values other than `admin`) will result in an error message.",
                  "chinese": "- 需要管理員權限。\n- 更改將持久保存到 `vote_settings.json`。\n- 無效輸入（例如，除 `admin` 外的非數字值）將導致錯誤消息。"}
    },
    {
        "name": "lang",
        "description": {"english": "Toggles the language of the bot's help panel between English and Chinese.",
                         "chinese": "切換機器人幫助面板的語言在英文和中文之間。"},
        "usage": "`!lang`",
        "arguments": {"english": "No arguments required.", "chinese": "無需參數。"},
        "notes": {"english": "- Toggles the language for the entire server.\n- Changes are saved persistently in `vote_settings.json`.\n- Affects only the `!help` command output.",
                  "chinese": "- 為整個伺服器切換語言。\n- 更改將持久保存到 `vote_settings.json`。\n- 僅影響 `!help` 命令的輸出。"}
    },
    {
        "name": "ask",
        "description": {"english": "Asks a question and receives a response based on a random success rate.",
                         "chinese": "提出問題並根據隨機成功率獲得回應。"},
        "usage": "`!ask [question]`",
        "arguments": {"english": "**question**: The question to ask (e.g., `Will it rain today?`).\n- Example: `!ask Will I win the lottery?`",
                      "chinese": "**問題**：要提出的問題（例如，`今天會下雨嗎？`）。\n- 示例：`!ask 我會中彩票嗎？`"},
        "notes": {"english": "- Responses are based on a random success rate (0-100%).\n- Higher success rates yield more positive responses; lower rates yield negative or uncertain responses.\n- The question is included in the response for context.",
                  "chinese": "- 回應基於隨機成功率（0-100%）。\n- 較高的成功率會產生更積極的回應；較低的成功率會產生否定或不確定的回應。\n- 問題將包含在回應中以提供上下文。"}
    },
    {
        "name": "pick",
        "description": {"english": "Randomly selects one option from a list of provided choices.",
                         "chinese": "從提供的選項列表中隨機選擇一個。"},
        "usage": "`!pick [choice1] [choice2] [choice3]...`",
        "arguments": {"english": "**choices**: A list of options to choose from (at least one required).\n- Example: `!pick apple banana orange`",
                      "chinese": "**選項**：要選擇的選項列表（至少需要一個）。\n- 示例：`!pick 蘋果 香蕉 橙子`"},
        "notes": {"english": "- At least one choice must be provided.\n- Choices are separated by spaces.\n- The bot will select one option randomly.",
                  "chinese": "- 必須提供至少一個選項。\n- 選項之間用空格分隔。\n- 機器人將隨機選擇一個選項。"}
    },
    {
        "name": "rng",
        "description": {"english": "Generates a random number between a specified minimum and maximum.",
                         "chinese": "在指定的最小值和最大值之間生成一個隨機數。"},
        "usage": "`!rng [min] [max] [int/float]`",
        "arguments": {"english": "**min**: (Optional) The minimum value (defaults to 1).\n**max**: (Optional) The maximum value (defaults to 100).\n**type**: (Optional) `int` or `float` to specify the number type (defaults to `int`).\n- Example: `!rng 1 10 int`, `!rng 0.0 1.0 float`",
                      "chinese": "**最小值**：（可選）最小值（默認為 1）。\n**最大值**：（可選）最大值（默認為 100）。\n**類型**：（可選）`int` 或 `float` 指定數字類型（默認為 `int`）。\n- 示例：`!rng 1 10 int`、`!rng 0.0 1.0 float`"},
        "notes": {"english": "- If type is not specified, integer is assumed.\n- Min and max must be valid numbers, and min must be less than or equal to max.\n- For floats, the result is rounded to 2 decimal places.",
                  "chinese": "- 如果未指定類型，假設為整數。\n- 最小值和最大值必須是有效數字，且最小值必須小於或等於最大值。\n- 對於浮點數，結果四捨五入到小數點後兩位。"}
    },
    {
        "name": "rcg",
        "description": {"english": "Generates a random color in hexadecimal format with a preview.",
                         "chinese": "生成一個隨機的十六進制格式顏色並附帶預覽。"},
        "usage": "`!rcg`",
        "arguments": {"english": "No arguments required.\n- Example: `!rcg`", "chinese": "無需參數。\n- 示例：`!rcg`"},
        "notes": {"english": "- Returns a random color in hexadecimal format (e.g., #FF5733) with a preview in an embed.\n- The color is generated by randomly selecting values for red, green, and blue channels.",
                  "chinese": "- 返回一個隨機的十六進制格式顏色（例如，#FF5733）並在嵌入中顯示預覽。\n- 顏色通過隨機選擇紅、綠、藍通道的值生成。"}
    },
    {
        "name": "copypasta",
        "description": {"english": "Posts a random copypasta from a chosen type's template pool.",
                         "chinese": "從所選類型的模板池中發送一則隨機迷因文本。"},
        "usage": "`!copypasta <type> <text>` (alias: `!cp`)",
        "arguments": {"english": "**type**: Which pool to pick from - `tag` (aliases: `name`, `person`, `mention`, `user`), `activity` (aliases: `thing`, `action`, `verb`), or `song` (aliases: `music`, `tune`), or any custom type an admin created.\n**text**: The word, name, mention, activity, or song title to slot into the template.\n- Example: `!copypasta tag @User`, `!copypasta activity digging`, `!copypasta song a song`.",
                      "chinese": "**類型**：要從哪個池中選取 - `tag`（別名：`name`、`person`、`mention`、`user`）、`activity`（別名：`thing`、`action`、`verb`）或 `song`（別名：`music`、`tune`），或管理員建立的自訂類型。\n**文字**：要填入模板的詞語、名稱、標註、活動或歌曲名稱。\n- 示例：`!copypasta tag @User`、`!copypasta activity digging`、`!copypasta song a song`。"},
        "notes": {"english": "- Each type keeps its own separate pool of templates, so a tagging line can never get mixed up with a song line or vice versa.\n- The bot avoids repeating the exact same template twice in a row for the same type on the same server.\n- Admins can manage pools live: `!copypasta list [search]` (opens a Previous/Next/Search/Close menu), `info <type>` (alias: `show`, paginated), `create <type>`, `delete <type>`, `enable/disable <type>`, `add <type> <template with {text}>`, `remove <type> <index>`.",
                  "chinese": "- 每個類型都有各自獨立的模板池，因此標註類型的句子不會與歌曲類型混用，反之亦然。\n- 機器人會避免在同一伺服器的同一類型中連續發送完全相同的模板。\n- 管理員可即時管理模板池：`!copypasta list [搜尋詞]`（開啟含上一頁／下一頁／搜尋／關閉的選單）、`info <類型>`（別名：`show`，分頁顯示）、`create <類型>`、`delete <類型>`、`enable/disable <類型>`、`add <類型> <包含 {text} 的模板>`、`remove <類型> <索引>`。"}
    },
    {
        "name": "chunithm",
        "description": {"english": "CHUNITHM tools: draw a song's player-population rating chart, search songs, and analyse your own imported scores.",
                         "chinese": "CHUNITHM 工具：產生曲目的玩家分布圖、搜尋曲目，並分析你匯入的個人成績。"},
        "usage": "`/chunithm <subcommand>` or `!chunithm <subcommand>`. Scores: `upload` `me` `song` `pattern` `suggest` `delete`. Charts: `rating` `song_search`. (`/` recommended)",
        "arguments": {"english": "**rating** `<song> [difficulty] [detailed]`: Draws a song's player-population chart (one chart per request). Wrap multi-word titles in quotes with `!`, e.g. `!chunithm rating \"XL TECHNO\" MASTER` (`/` has autocomplete and needs no quotes). `difficulty` is `BASIC`, `ADVANCED`, `EXPERT`, `MASTER` (default) or `ULTIMA`; `detailed:true` shows the full score and player range instead of the SS-to-MAX focus.\n**song_search** `<query>`: Searches the song list (title, alias or fragment) and shows up to 10 candidates with their difficulties and an `id:` you can pass to `rating`. Never fetches statistics.\n**upload** `<file>`: Attach your `chunithm-player-data_*.json` export.\n**me**: Rating, level, played charts, SSS+/SS+/FC/AJ counts.\n**song** `<title>`: Your scores on a song (`/` autocompletes from your played songs).\n**pattern** `[all|best|rating]`: Strong and weak note patterns over all played 14+ charts, Best 30, or Best 30 + New 20.\n**suggest**: Songs to push your rating (your strong patterns) and to practice (your weak patterns).\n**delete**: Remove your stored data.\n- Examples: `/chunithm rating song:... difficulty:MASTER`, `!chunithm song_search サファリ`, `/chunithm pattern scope:best`",
                      "chinese": "**rating** `<曲名> [難度] [詳細]`：產生曲目的玩家分布圖（每次一張）。使用 `!` 時，含空格的曲名請加引號，例如 `!chunithm rating \"XL TECHNO\" MASTER`（`/` 版本有自動完成，無需引號）。難度可選 `BASIC`、`ADVANCED`、`EXPERT`、`MASTER`（預設）或 `ULTIMA`；`detailed:true` 顯示完整分數與玩家範圍，預設為 SS 至 MAX 的聚焦視圖。\n**song_search** `<關鍵字>`：搜尋曲目清單（曲名、別名或片段），最多顯示 10 個候選曲目、難度，以及可用於 `rating` 的 `id:`。不會抓取統計資料。\n**upload** `<檔案>`：附上你的 `chunithm-player-data_*.json` 匯出檔。\n**me**：Rating、等級、已遊玩譜面數、SSS+/SS+/FC/AJ 數量。\n**song** `<曲名>`：你在某首曲目的成績（`/` 版本會從你玩過的曲目自動完成）。\n**pattern** `[all|best|rating]`：依所有已玩 14+ 譜面、Best 30 或 Best 30 + New 20，分析擅長與不擅長的配置。\n**suggest**：推薦推分曲（你擅長的配置）與練習曲（你不擅長的配置）。\n**delete**：刪除你已儲存的資料。\n- 示例：`/chunithm rating song:... difficulty:MASTER`、`!chunithm song_search サファリ`、`/chunithm pattern scope:best`"},
        "notes": {"english": "- Score replies with `/` are only visible to you; with `!` they are public and an attached file stays in the channel, so use `/chunithm upload`.\n- Re-uploading replaces your old data; only played charts are stored.\n- `pattern` and `suggest` use the community chart sheet (14+ charts only); charts missing from it are ignored.\n- Skill values are relative to your own level and aim scores are estimates.\n- Suggestions also read the sheet's notes (error tolerance, personal difference, not-for-push).\n- `rating` and `song_search` search offline and rank by relevance; only the chart you pick is processed.",
                  "chinese": "- 使用 `/` 時成績相關回覆只有你看得到；使用 `!` 時回覆是公開的，附件也會留在頻道中，所以請用 `/chunithm upload`。\n- 重新上傳會取代舊資料；只儲存已遊玩的譜面。\n- `pattern` 與 `suggest` 使用社群譜面表（僅限 14+ 譜面）；表中沒有的譜面會被忽略。\n- 實力數值是相對於你自己的水平，目標分數為估算值。\n- 推薦也會參考譜面表的說明（容錯、個人差、不適合推分）。\n- `rating` 與 `song_search` 離線搜尋並依相關度排序；只會處理你選擇的那張圖表。"}
    },
    {
        "name": "feed",
        "description": {"english": "Tracks social media accounts and posts their new posts to a channel. Currently supports Bluesky.",
                         "chinese": "追蹤社群媒體帳號，並將新貼文發送到指定頻道。目前支援 Bluesky。"},
        "usage": "`/feed <subcommand>` or `!feed <subcommand>` (admin only). Subcommands: `add` `remove` `list` `check`. (`/` recommended)",
        "arguments": {"english": "**add** `<account> [channel] [platform]`: Starts tracking an account. `account` is a profile URL or handle. `channel` is where posts go (defaults to the current channel; with `!` use a #mention, no ID needed; `/` gives a channel picker). `platform` defaults to `bluesky`.\n**remove** `<account>`: Stops tracking that account.\n**list**: Shows tracked accounts and their channels.\n**check**: Checks for new posts immediately.\n- Examples: `/feed add account:https://bsky.app/profile/performaien.bsky.social channel:#news`, `!feed add performaien.bsky.social #news`, `!feed remove performaien.bsky.social`",
                      "chinese": "**add** `<帳號> [頻道] [平台]`：開始追蹤帳號。`帳號` 可以是個人檔案網址或帳號名稱。`頻道` 是貼文發送的位置（預設為目前頻道；使用 `!` 時直接標註 #頻道，不需要 ID；`/` 版本提供頻道選單）。`平台` 預設為 `bluesky`。\n**remove** `<帳號>`：停止追蹤該帳號。\n**list**：顯示已追蹤的帳號及其頻道。\n**check**：立即檢查是否有新貼文。\n- 示例：`/feed add account:https://bsky.app/profile/performaien.bsky.social channel:#news`、`!feed add performaien.bsky.social #news`、`!feed remove performaien.bsky.social`"},
        "notes": {"english": "- Requires the Manage Server permission.\n- Checks every 5 minutes. Only posts made after you add the account are sent; old posts are not reposted.\n- Replies are skipped; reposts are included and labeled.\n- Replies to `list` and errors are only visible to you when using `/`.",
                  "chinese": "- 需要「管理伺服器」權限。\n- 每 5 分鐘檢查一次。只會發送加入帳號之後的新貼文，不會重發舊貼文。\n- 回覆貼文會被略過；轉發貼文會包含並標示。\n- 使用 `/` 時，`list` 的回覆與錯誤訊息只有你看得到。"}
    },
    {
        "name": "chat",
        "description": {"english": "Talks to the bot's local AI model. Everyone in a channel shares one conversation.",
                         "chinese": "與機器人的本地 AI 模型對話。同一頻道的所有人共用同一個對話。"},
        "usage": "`!chat <message>` or `/chat` (or just @mention the bot)",
        "arguments": {"english": "**message**: What you want to say to the AI.\n**image**: (Optional, slash only) An image to show it. With `!`, just attach the image to your message.\n- Example: `!chat what's a good rhythm game for beginners?`",
                      "chinese": "**訊息**：你想對 AI 說的話。\n**圖片**：（可選，僅限斜線指令）要讓它看的圖片。使用 `!` 時，直接在訊息中附上圖片即可。\n- 示例：`!chat 有什麼適合新手的音樂遊戲？`"},
        "notes": {"english": "- Each channel has its own conversation; channels never share context.\n- A conversation expires after 15 minutes of silence.\n- Replies can be slow because the model runs locally.\n- Use `!chatreset` to clear the channel's conversation.",
                  "chinese": "- 每個頻道有自己的對話，頻道之間不會共用內容。\n- 對話在 15 分鐘無人發言後過期。\n- 由於模型在本機運行，回覆可能較慢。\n- 使用 `!chatreset` 清除該頻道的對話。"}
    },
    {
        "name": "chatreset",
        "description": {"english": "Clears this channel's shared AI conversation.",
                         "chinese": "清除此頻道共用的 AI 對話。"},
        "usage": "`!chatreset` or `/chatreset`",
        "arguments": {"english": "No arguments required.\n- Example: `!chatreset`",
                      "chinese": "無需參數。\n- 示例：`!chatreset`"},
        "notes": {"english": "- Clears the conversation for everyone in the channel, not just you.",
                  "chinese": "- 會為頻道內所有人清除對話，而不只是你自己。"}
    },
    {
        "name": "autoban",
        "description": {"english": "Bans anyone who sends a message in this channel (Admin only).",
                         "chinese": "封鎖任何在此頻道發送訊息的人（僅限管理員）。"},
        "usage": "`!autoban [reason] [delete_days]`",
        "arguments": {"english": "**reason**: (Optional) Ban reason shown in the audit log. If omitted, disables autoban in this channel.\n**delete_days**: (Optional) Days of the member's message history to delete, 0-7 (default 0).\n- Example: `!autoban \"honeypot channel\" 1`, `!autoban` (disables it)",
                      "chinese": "**原因**：（可選）顯示於審核日誌的封鎖原因。如果省略，則停用此頻道的自動封鎖。\n**delete_days**：（可選）要刪除該成員多少天內的訊息，0 至 7（預設 0）。\n- 示例：`!autoban \"honeypot channel\" 1`、`!autoban`（停用）"},
        "notes": {"english": "- Requires Administrator permission.\n- The bot needs the `ban_members` permission.\n- Intended for honeypot channels; anyone who types there is banned immediately.",
                  "chinese": "- 需要管理員權限。\n- 機器人需要 `ban_members` 權限。\n- 適用於誘捕頻道；任何在此發言的人都會立即被封鎖。"}
    },
    {
        "name": "setperms",
        "description": {"english": "Grants permissions to a specific role in a specific channel (Admin only).",
                         "chinese": "在特定頻道中為特定角色授予權限（僅限管理員）。"},
        "usage": "`!setperms <channel_id> <role_id>`",
        "arguments": {"english": "**channel_id**: The ID of the channel to modify permissions for.\n**role_id**: The ID of the role to grant permissions to.",
                      "chinese": "**頻道 ID**：要修改權限的頻道 ID。\n**角色 ID**：要授予權限的角色 ID。"},
        "notes": {"english": "- Requires administrator permissions.\n- The bot must have `manage_channels` permission.\n- Grants view, send messages, and read message history permissions to the role.\n- Use Discord Developer Mode to get channel and role IDs.",
                  "chinese": "- 需要管理員權限。\n- 機器人必須具有 `manage_channels` 權限。\n- 為角色授予查看、發送消息和閱讀消息歷史記錄的權限。\n- 使用 Discord 開發者模式獲取頻道和角色 ID。"}
    },
    {
        "name": "autoreact",
        "description": {"english": "Sets an emoji to auto-react to messages from a specific user or all messages in the channel, or disables auto-reactions.",
                         "chinese": "設置一個表情符號以自動對頻道中特定用戶或所有消息進行反應，或禁用自動反應。"},
        "usage": "`!autoreact [emoji] [user]`",
        "arguments": {"english": "**emoji**: (Optional) The emoji to auto-react with. If omitted, disables auto-reactions in the channel.\n**user**: (Optional) The user whose messages to auto-react to (must be mentioned, e.g., `@User`). If omitted, reacts to all messages.\n- Example: `!autoreact 😊 @User`, `!autoreact 😊`, `!autoreact` (disables auto-reactions)",
                      "chinese": "**表情符號**：（可選）用於自動反應的表情符號。如果省略，則禁用頻道中的自動反應。\n**用戶**：（可選）要自動反應的用戶消息（必須提及，例如，`@User`）。如果省略，則對所有消息進行反應。\n- 示例：`!autoreact 😊 @User`、`!autoreact 😊`、`!autoreact`（禁用自動反應）"},
        "notes": {"english": "- No special permissions required for users.\n- The bot must have `add_reactions` permission.\n- Settings are saved persistently in `vote_settings.json`.\n- Only one emoji can be set per channel, and it applies to either a specific user or all messages.",
                  "chinese": "- 用戶無需特殊權限。\n- 機器人必須具有 `add_reactions` 權限。\n- 設置將持久保存到 `vote_settings.json`。\n- 每個頻道只能設置一個表情符號，且適用於特定用戶或所有消息。"}
    },
    {
        "name": "keyword",
        "description": {"english": "Manages global keyword-triggered response sets (Admin only).",
                         "chinese": "管理全局關鍵詞觸發回應組（僅限管理員）。"},
        "usage": "`!keyword <list|info|create|delete|enable|disable|addkeyword|removekeyword|addresponse|removeresponse> ...`",
        "arguments": {"english": "See `!keyword` with no arguments for the full subcommand list, or `!help <subcommand>`-style usage per subcommand isn't available - subcommands are documented in the `!keyword` group message itself.\n- `!keyword list [search]`: opens a Previous/Next/Search/Close button menu showing keyword sets a few at a time. Pass a search term to start filtered (e.g. `!keyword list cry`), or use the Search button in the menu to filter/change filter by set name or keyword any time.",
                      "chinese": "使用 `!keyword`（不帶參數）查看完整子命令列表；子命令用法已在 `!keyword` 群組訊息中說明。\n- `!keyword list [搜尋詞]`：開啟含「上一頁／下一頁／搜尋／關閉」按鈕的選單，分頁顯示關鍵詞組。可直接帶搜尋詞開啟已篩選的畫面（例如 `!keyword list cry`），或隨時使用選單中的搜尋按鈕依組名或關鍵詞篩選。"},
        "notes": {"english": "- Requires administrator permissions.\n- Keyword sets are global: shared across every server the bot is in.\n- Changes are saved persistently in `data/keyword_sets.json`.\n- `!keyword list` shows 5 sets per page; use `!keyword info <id>` (alias: `show`) for a set's full keyword/response detail.",
                  "chinese": "- 需要管理員權限。\n- 關鍵詞組是全局的：在機器人所在的每個伺服器間共享。\n- 更改將持久保存到 `data/keyword_sets.json`。\n- `!keyword list` 每頁顯示 5 個關鍵詞組；使用 `!keyword info <id>`（別名：`show`）查看單一組的完整關鍵詞／回應詳情。"}
    },
]


class HelpMenu(discord.ui.View):
    def __init__(self, ctx, command_list, language, timeout=180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.command_list = command_list
        self.language = language
        self.current_page = 0

        # Labels depend on this guild's language, so they're set here
        # rather than at class-definition time (see module docstring).
        self.previous_button.label = t(language, "Previous", "上一頁")
        self.next_button.label = t(language, "Next", "下一頁")
        self.close_button.label = t(language, "Close", "關閉")

    def get_embed(self):
        command = self.command_list[self.current_page]
        embed = discord.Embed(
            title=t(self.language, f"Command: !{command['name']}", f"命令：!{command['name']}"),
            description=command["description"][self.language],
            color=discord.Color.blue()
        )
        embed.add_field(name=t(self.language, "🔹 Usage", "🔹 使用方法"), value=command["usage"], inline=False)
        embed.add_field(name=t(self.language, "🔹 Arguments", "🔹 參數"), value=command["arguments"][self.language], inline=False)
        embed.add_field(name=t(self.language, "🔹 Notes", "🔹 注意事項"), value=command["notes"][self.language], inline=False)
        embed.set_footer(text=t(self.language,
            f"Page {self.current_page + 1}/{len(self.command_list)} | Use !help for the full user manual.",
            f"第 {self.current_page + 1}/{len(self.command_list)} 頁 | 使用 !help 獲取完整的使用手冊。"))
        return embed

    @discord.ui.button(style=discord.ButtonStyle.grey)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.current_page = (self.current_page - 1) % len(self.command_list)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        self.current_page = (self.current_page + 1) % len(self.command_list)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return
        await interaction.response.edit_message(
            content=t(self.language, "Help menu closed.", "幫助選單已關閉。"), embed=None, view=None)
        self.stop()


class HelpCog(commands.Cog, name="help"):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="help",
        description="Displays the user manual for the bot or specific command details.")
    @discord.app_commands.describe(command="Get detailed help for one specific command (optional)")
    async def help(self, ctx, *, command: str = None):
        """Displays the user manual for the bot or specific command details."""
        language = get_guild_language(load_settings(), ctx.guild.id)

        if not command:
            embed = discord.Embed(
                title=t(language, "Bot User Manual", "機器人使用手冊"),
                description=t(language,
                    "Welcome to the bot! Use the buttons below to browse command details.",
                    "歡迎使用本機器人！使用下面的按鈕瀏覽命令詳細信息。"),
                color=discord.Color.blue()
            )
            embed.add_field(
                name=t(language, "🔹 Command Prefix", "🔹 命令前綴"),
                value=t(language, "Use `!` as the command prefix (e.g., `!help`).", "使用 `!` 作為命令前綴（例如，`!help`）。"),
                inline=False
            )
            embed.add_field(
                name=t(language, "🔹 Features", "🔹 功能"),
                value=t(language,
                    "**Keyword Responses**: Sends copypastas for specific keywords in messages (manage sets with `!keyword`).\n"
                    "**Repeat Detection**: Echoes a message if three different users send it consecutively.\n"
                    "**Timeout Voting**: Use `!vto` to vote for timing out a member. Configurable via `!setvote`. Supports multiple votes at once.",
                    "**關鍵詞回應**：對消息中的特定關鍵詞回應迷因文本（使用 `!keyword` 管理關鍵詞組）。\n"
                    "**重複檢測**：若三個不同用戶連續發送相同消息，則重複該消息。\n"
                    "**暫停投票**：使用 `!vto` 投票暫停成員。可通過 `!setvote` 配置。支持同時多個投票。"),
                inline=False
            )
            embed.add_field(
                name=" ",
                value=t(language,
                    "**Random Response**: `!ask` gives answers based on a random success rate.\n"
                    "**Random Choice**: `!pick` selects one option from a list.\n"
                    "**Random Number**: `!rng` generates a number in a range.\n"
                    "**Random Color**: `!rcg` creates a hex color with a preview.\n"
                    "**Permissions**: `!setperms` grants channel access (admin only).\n"
                    "**Auto-Reactions**: `!autoreact` sets emoji reactions for messages.\n"
                    "**CHUNITHM**: `/chunithm rating` draws a song's player-population chart; `/chunithm song_search` finds songs; `/chunithm upload` imports your player data and `/chunithm pattern` / `/chunithm suggest` analyse it.\n"
                    "**Feed Tracker**: `/feed add` tracks a Bluesky account and posts its new posts to a channel; `/feed list`, `/feed remove` and `/feed check` manage it (admin only).\n"
                    "**AI Chat**: `!chat` or @mention the bot to talk to the local AI model; `!chatreset` clears it.",
                    "**隨機回應**：`!ask` 根據隨機成功率回應。\n"
                    "**隨機選擇**：`!pick` 從選項列表中選一個。\n"
                    "**隨機數**：`!rng` 生成範圍內的數字。\n"
                    "**隨機顏色**：`!rcg` 生成十六進制顏色並預覽。\n"
                    "**權限**：`!setperms` 授予頻道權限（僅限管理員）。\n"
                    "**自動反應**：`!autoreact` 為消息設置表情反應。\n"
                    "**CHUNITHM**：`/chunithm rating` 產生曲目的玩家分布圖；`/chunithm song_search` 搜尋曲目；`/chunithm upload` 匯入玩家資料，`/chunithm pattern` 與 `/chunithm suggest` 進行分析。\n"
                    "**動態追蹤**：`/feed add` 追蹤 Bluesky 帳號並將新貼文發送到頻道；`/feed list`、`/feed remove`、`/feed check` 用於管理（僅限管理員）。\n"
                     "**AI 對話**：使用 `!chat` 或提及機器人與本地 AI 模型對話；`!chatreset` 清除對話。"),
                inline=False
            )
            embed.add_field(
                name=t(language, "🔹 Navigation", "🔹 導航"),
                value=t(language,
                    "Use the buttons below to browse individual command details.",
                    "使用下面的按鈕瀏覽各個命令的詳細信息。"),
                inline=False
            )
            embed.set_footer(text=t(language,
                "Bot created for fun and moderation. Contact natherox through Discord for issues.",
                "機器人為娛樂和管理而創建。如有問題，請於Discord聯繫natherox。"))

            view = HelpMenu(ctx, COMMAND_LIST, language)
            await ctx.send(embed=embed, view=view)
            return

        command = (command.lower().lstrip("!/").split() or [""])[0]   # "chunithm rating" -> "chunithm"
        # rating / song_search / score used to be their own top-level commands
        command = {"rating": "chunithm", "song_search": "chunithm", "score": "chunithm"}.get(command, command)
        selected = next((cmd for cmd in COMMAND_LIST if cmd["name"] == command), None)
        if selected:
            embed = discord.Embed(
                title=t(language, f"Command: !{command}", f"命令：!{command}"),
                description=selected["description"][language],
                color=discord.Color.blue()
            )
            embed.add_field(name=t(language, "🔹 Usage", "🔹 使用方法"), value=selected["usage"], inline=False)
            embed.add_field(name=t(language, "🔹 Arguments", "🔹 參數"), value=selected["arguments"][language], inline=False)
            embed.add_field(name=t(language, "🔹 Notes", "🔹 注意事項"), value=selected["notes"][language], inline=False)
            embed.set_footer(text=t(language, "Use !help for the full user manual.", "使用 !help 獲取完整的使用手冊。"))
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title=t(language, "Error", "錯誤"),
                description=t(language,
                    f"No command named `{command}` found. Use `!help` to see all available commands.",
                    f"未找到名為 `{command}` 的命令。使用 `!help` 查看所有可用命令。"),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))