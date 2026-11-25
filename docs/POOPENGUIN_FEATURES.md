# Poopenguin Features

## 中文功能索引

非 CHUNITHM 功能集中在 `chuni_penguin/cogs/poopenguin/`，資料仍沿用 `data/poopenguin/`。這輪整理資料夾與相容路徑，娛樂面板保持既有操作。

| 功能 | 內容 | 啟用狀態 |
|---|---|---|
| 隨機工具 | 問企鵝、幫我選、隨機整數／小數、隨機顏色 | 預設啟用 |
| 抽卡 | 單抽／十抽、個人目標與保底、紀錄、確認重置、角色瀏覽、機率、擁有者管理卡池與圖片 | 預設啟用 |
| 文字模板 | 瀏覽／搜尋、隨機套用與填空、伺服器管理員新增／刪除／開關／編輯共用模板 | 預設啟用 |
| 關鍵字 | 瀏覽／搜尋、觸發詞與回應、獨立觸發機率、伺服器管理員管理共用關鍵字組 | 管理／瀏覽預設啟用；自動回應需訊息模組 |
| 佩麗卡經文回覆 | 辨識中英文經文，回覆整節原文、章節與譯本 | 選配，可獨立啟用 |
| 訊息處理 | 自動反應、自動封鎖規則、提及時 Help／本機 AI、關鍵字及重複訊息回聲 | 沿用選配設定 |
| 投票禁言 | 時間設定、反應票數、門檻、限制管理員投票 | 沿用選配設定 |
| 伺服器管理 | 頻道／身分組權限、自動反應／封鎖與語言設定 | 沿用選配設定 |
| 動態追蹤 | Bluesky 帳號追蹤、列出／移除、手動檢查、定時輪詢 | 沿用選配設定 |
| 本機 AI | LM Studio 文字對話、圖片描述、頻道對話記憶與重置 | 沿用選配設定，需原本機服務 |

驗證包含實際指令盤點、匯入相容、載入生命週期、抽卡保底／紀錄／重置、模板及關鍵字管理、操作權限與隔離測試。選配管理／動態／AI 以替身服務驗證，不對真實成員執行管理動作，也不自動啟用外部服務；實際外部服務連線仍依原部署設定與人工驗收。

## 佩麗卡經文回覆

在 `bot.ini` 的 `[cogs]` 把 `poopenguin.scripture_cog` 加入 `extra`，重新啟動後生效。它可以單獨啟用，預設關閉。索引從本機檔案建立，不需要額外服務或 Discord token。

在 Bot 可見的伺服器頻道輸入「你手裡拿的是什麼？」時，預設譯本會回覆：

> 「耶和華對摩西說：「你手裡拿的是什麼？」摩西說：「是一根手杖。」」<br>
> 出埃及記 4:2（繁中當代譯本）

- 適用於新的一般訊息及回覆。略過私訊、其他機器人、自身、Webhook、系統訊息、編輯事件、空內容與有效文字指令。每則最多一筆經文回覆，關閉所有提及通知。
- 原版預設讀取繁中新標點和合本、繁中當代譯本及英文 WEB，共 93,080 筆。BM25 取前 24 個候選，整句與完整經節、節內文字比對，門檻為 0.85。BM25 保留英文字界，忽略大小寫、標點及全半形，支援原有的「裏／裡」「甚麼／什麼」對應。內建常用語及過短查詢會略過。原文與譯本以實際收錄的資料為準。[譯本來源、授權、重建方式](../chuni_penguin/cogs/poopenguin/scripture_cog/DATA-SOURCES.md)
- 使用本機自訂設定時，把原 Cog 的 `settings.json` 和其引用的譯本照原相對路徑放入 `data/poopenguin/scripture/`。沒有自訂設定時使用隨附的版本。保留 `channel_ids`（十進位 ID 字串，空清單表示全部可見伺服器頻道）、`editions`（依序決定同文優先譯本）、`blocked_phrases`、`match_mode`（`strict`、`variants`、`bm25`）、`match_threshold`、`aliases`。譯本路徑以設定檔所在目錄為準，設定與語料在重載成功後生效。若重載因設定或語料錯誤失敗，沿用上次成功的索引及頻道設定。
- 若同時開啟 `messages_cog`，沿用原訊息處理順序。有效指令、自動封鎖已處理的訊息及專屬提及流程優先。一般聊天照常執行自動反應、關鍵字與三人複誦，之後才交給經文偵測。符合多項時可能同時看到原聊天回應及一則經文。原獨立版沒有 Poopenguin 關鍵字或複誦規則。
- 全域冷卻 3 秒、最近 256 個已回覆訊息 ID 及最多兩個同時搜尋均沿用原設計。忙碌時略過額外搜尋。冷卻與 ID 在 Bot 存活期間跨 Cog 重載保留，重新啟動後清空。偵測器不寫入聊天內容、訊息 ID 或匹配結果。發送失敗的紀錄只帶 HTTP 狀態碼。
- 需要 Guild Messages、Message Content intents。Developer Portal 亦須開啟 Message Content 並在需要時取得核准；Gateway 4014 表示請求的 privileged intent 未獲准。單則空訊息也可能只有附件，無法由此判斷權限。頻道須允許檢視頻道、發送訊息及讀取訊息歷史，在討論串需要「在討論串發送訊息」。[Discord Gateway intents](https://docs.discord.com/developers/events/gateway#message-content-intent) · [Discord 訊息回覆](https://docs.discord.com/developers/resources/message#create-message)

以下保留完整指令及參數清單。

Poopenguin contains the non-CHUNITHM commands and message behavior that were
previously implemented under `chuni_penguin.legacy_b`. The active code now lives
under `chuni_penguin.cogs.poopenguin`; the old import names remain aliases to the
same module objects for configuration and extension compatibility.

The four default extensions are `general_cog`, `gacha_cog`, `copypasta_cog`, and
`keywords_cog`. The optional extensions `messages_cog`, `vote_cog`, `admin_cog`,
`feed_cog`, `ai_cog`, and `scripture_cog` stay disabled unless explicitly enabled.

## Commands and behavior

| Cog | Commands | What it does |
| --- | --- | --- |
| `general_cog` | `ask <question>`, `pick <choices>`, `rng [min_val=1] [max_val=100] [type=int]`, `rcg` | Guild-only random answers, quoted multiword choice selection, integer or float ranges, and a random color embed. The commands use hybrid prefix/slash registration. |
| `gacha_cog` | `gacha`, `gacha pull`, `gacha target [character]`, `gacha stats` (`results` alias), `gacha reset`, `gacha browse`, `gacha pool`, and owner-only `setfeatured`, `setimage`, `removeimage`, `setthumbnail`, `removethumbnail`, `reload` | Free single and ten-pull menus, personal pity targets, collection statistics, confirmed self-reset, roster browsing, rates, and artwork controls. A target is guaranteed within 200 pulls; each user's pity advances independently. `/gacha pull` opens the menu for slash users. |
| `copypasta_cog` | `copypasta <type> <values...>` (`cp` alias), `list [search]`, `info` (`show` alias), and `create`, `delete`, `enable`, `disable`, `add`, `remove` | Global template pools with named placeholders, random selection, type browsing, search, autocomplete, and per-template fill-in menus. Edits require the Administrator permission in the invoking guild. |
| `keywords_cog` | `keyword`, `list [search]`, `info` (`show` alias), and `create`, `delete`, `enable`, `disable`, `addkeyword`, `removekeyword`, `addresponse`, `removeresponse`, `rate` | Global case-insensitive substring triggers with independently configurable response chance. Server members can browse, and edits require the Administrator permission in the invoking guild. |
| `scripture_cog` (optional) | Chat listener | Perlica local quotation lookup and a mention-free reply with the full verse, citation and edition. |
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
