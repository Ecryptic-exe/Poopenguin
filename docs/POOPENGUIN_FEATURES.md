# Poopenguin Features

## 中文功能索引

非 CHUNITHM 功能集中在 `chuni_penguin/cogs/poopenguin/`，資料仍沿用 `data/poopenguin/`。這輪整理資料夾與相容路徑，娛樂面板保持既有操作。

| 功能 | 內容 | 啟用狀態 |
|---|---|---|
| 隨機工具 | 問企鵝、幫我選、隨機整數／小數、隨機顏色 | 預設啟用 |
| 抽卡 | 單抽／十抽、個人目標與保底、紀錄、確認重置、角色瀏覽、機率、擁有者管理卡池與圖片 | 預設啟用 |
| 文字模板 | 瀏覽／搜尋、隨機套用與填空、擁有者新增／刪除／開關／編輯模板 | 預設啟用 |
| 關鍵字 | 瀏覽／搜尋、觸發詞與回應、獨立觸發機率、擁有者完整管理 | 管理／瀏覽預設啟用；自動回應需訊息模組 |
| 訊息處理 | 自動反應、自動封鎖規則、提及時 Help／本機 AI、關鍵字及重複訊息回聲 | 沿用選配設定 |
| 投票禁言 | 時間設定、反應票數、門檻、限制管理員投票 | 沿用選配設定 |
| 伺服器管理 | 頻道／身分組權限、自動反應／封鎖與語言設定 | 沿用選配設定 |
| 動態追蹤 | Bluesky 帳號追蹤、列出／移除、手動檢查、定時輪詢 | 沿用選配設定 |
| 本機 AI | LM Studio 文字對話、圖片描述、頻道對話記憶與重置 | 沿用選配設定，需原本機服務 |

驗證包含實際指令盤點、匯入相容、載入生命週期、抽卡保底／紀錄／重置、模板及關鍵字管理、操作權限與隔離測試。選配管理／動態／AI 以替身服務驗證，不對真實成員執行管理動作，也不自動啟用外部服務；實際外部服務連線仍依原部署設定與人工驗收。

以下保留完整指令及參數清單。

Poopenguin contains the non-CHUNITHM commands and message behavior that were
previously implemented under `chuni_penguin.legacy_b`. The active code now lives
under `chuni_penguin.cogs.poopenguin`; the old import names remain aliases to the
same module objects for configuration and extension compatibility.

The four default extensions are `general_cog`, `gacha_cog`, `copypasta_cog`, and
`keywords_cog`. The optional extensions `messages_cog`, `vote_cog`, `admin_cog`,
`feed_cog`, and `ai_cog` stay disabled unless explicitly enabled.

## Commands and behavior

| Cog | Commands | What it does |
| --- | --- | --- |
| `general_cog` | `ask <question>`, `pick <choices>`, `rng [min_val=1] [max_val=100] [type=int]`, `rcg` | Guild-only random answers, quoted multiword choice selection, integer or float ranges, and a random color embed. The commands use hybrid prefix/slash registration. |
| `gacha_cog` | `gacha`, `gacha pull`, `gacha target [character]`, `gacha stats` (`results` alias), `gacha reset`, `gacha browse`, `gacha pool`, and owner-only `setfeatured`, `setimage`, `removeimage`, `setthumbnail`, `removethumbnail`, `reload` | Free single and ten-pull menus, personal pity targets, collection statistics, confirmed self-reset, roster browsing, rates, and artwork controls. A target is guaranteed within 200 pulls; each user's pity advances independently. `/gacha pull` opens the menu for slash users. |
| `copypasta_cog` | `copypasta <type> <values...>` (`cp` alias), `list [search]`, `info` (`show` alias), and owner-only `create`, `delete`, `enable`, `disable`, `add`, `remove` | Global template pools with named placeholders, random selection, type browsing, search, autocomplete, and per-template fill-in menus. Types and templates are managed by the bot owner. |
| `keywords_cog` | `keyword`, `list [search]`, `info` (`show` alias), and owner-only `create`, `delete`, `enable`, `disable`, `addkeyword`, `removekeyword`, `addresponse`, `removeresponse`, `rate` | Global case-insensitive substring triggers with independently configurable response chance. Read-only browsing is available to server members; edits are restricted to the bot owner. |
| `messages_cog` (optional) | Message listener | Applies configured auto-ban and auto-reaction rules, routes mentions to shared help or local AI, checks keyword sets, and echoes three consecutive matching messages from distinct users. Server administrators are exempt from auto-ban. |
| `vote_cog` (optional) | `vto <member> [time_str]`, `setvote <count\|admin>`, reaction listener | Opens a timeout vote, records one vote per person, enforces optional admin-only voting, and applies the configured threshold. Durations accept `d`, `h`, `m`, `s`, or `random`. |
| `admin_cog` (optional) | `setperms <channel_id> <role_id>`, `autoreact [emoji] [user]`, `autoban [reason] [delete_days=0]`, `lang` | Configures channel access, automatic reactions, automatic bans, and the guild's help/response language. Existing command checks and permissions are preserved. |
| `feed_cog` (optional) | `feed`, `add <account> [channel] [platform=bluesky]`, `remove <account>`, `list`, `check` | Tracks Bluesky accounts, sends new posts to configured channels, and polls every five minutes. Commands require Manage Server permission. |
| `ai_cog` (optional) | `chat [image] <prompt>`, `chatreset` | Uses a local LM Studio OpenAI-compatible endpoint. Channel conversations are shared per channel, retained in memory, and reset by `chatreset`; image descriptions are also produced by the configured local model. The extension requires loopback `LM_STUDIO_BASE_URL` and `LM_STUDIO_MODEL` settings. |

All feature state remains under `data/poopenguin/`: `vote_settings.json`,
`votes.json`, `keyword_sets.json`, `copypasta_sets.json`, `gacha_pool.json`,
`gacha_users.json`, `gacha_images.json`, and `feeds.json`. The JSON storage
helpers retain their backup and fail-closed behavior. The AI conversation history
is in memory and is not written to those files.

The shared bot help command remains the sole help implementation. Poopenguin
does not register its own help or command-tree sync command; command registration
continues through the shared application lifecycle.
