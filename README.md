# CHUNITHM Penguin v2

以 A 的 Song／Chart 資料庫、登入、成績流程與多分頁資訊卡為基礎，整合 B 的使用者功能與 C 已完成的離線統計產圖。工作目錄為 `D:\WorkSpace\Codex_WorkSpace\LB_Penguin`。

目前可執行本機版本，**已用既有 `.env` Token 登入並保持 Gateway 連線**。2026-10-19 23:37 更新：已補上一般選歌的成績分佈資料取得流程，按可信 Chunirec ID 抓公開統計，使用 C 完整視圖與 24 小時快取。原三來源 Account、`/chart`／`/score` 八項選單及 Poopenguin 獨立資料夾保留。完整 **698 項測試通過**，另實測三張非範例譜面的真正資料／產圖／PNG 附件。新程序 **16532**、21 模組、Gateway、資料庫及 **834 筆社群資料**正常。14 個斜線入口、原 Compare、資料及 ID 關聯保留。[分佈功能與實測](docs/SCORE_DISTRIBUTION.md)。

實際逐層內容／按鈕／選單／動態規則見 [Account／Song 介面清單](docs/EMBED_UX_PROPOSAL.md)。評價寫入、匿名範圍與維護見 [玩家評價說明](docs/CHART_REVIEWS.md)，社群資料操作與回退見 [譜面資料庫說明](docs/ANNOTATION_DATABASE.md)。真人來源／視覺驗收與 Chunilib 線上登入限制見 DELIVERY.md。

從 GitHub 下載後請先看 [新環境安裝](docs/GITHUB_SETUP.md)。儲存庫不含 Token、玩家資料庫或本機執行紀錄，設定需使用自己的 `.env`。

## 啟動

2026-10-16 22:18 修正：同首歌曲的一般／WORLD’S END 版本整合為一張卡，特殊版本由難度選單切換；`gengaozo` 不再跳出四個資料庫 ID。原歌曲／譜面關聯保留，600 項完整測試通過。

使用 Python 3.14 與 uv，於此目錄執行：

```powershell
uv sync --no-dev --extra rating
uv run --no-sync python -X utf8 -m penguin check
```

初次建置的 `data/penguin.sqlite3` 含 2,288 首歌曲、8,312 張譜面，目前持續沿用既有資料。全新安裝才執行以下命令；檔案已存在時會拒絕覆寫：

```powershell
uv run --no-sync python -X utf8 -m penguin init
```

複製 `bot.example.ini` 為 `bot.ini`（已有設定時保留原檔）。本次已將授權沿用的 Token 放到本目錄 `.env`，沒有覆寫來源檔。全新安裝可參照 `.env.example` 建立自己的 `.env`；基本啟動只需 `TOKEN`。然後執行：

```powershell
uv run --no-sync python -X utf8 launcher.py --env-file .env
```

`.env.example` 列出實際支援的選項：文字前綴、明確啟用／停用模組、Kamaitachi 憑證、本機 LM Studio 與唯讀測試參數。留白沿用 `bot.ini`，修改後重新啟動；不展開 `${...}` 或執行任何內容，也不從 `.env` 更換資料庫位置。未知欄位不套用。

正常啟動會在資料庫與 extension 載入完成、命令樹通過安全檢查後，將目前全域 slash command tree 同步到 Discord 一次。載入不完整、資料庫未就緒或 Discord 拒絕同步時，Bot 會記錄原因並繼續啟動；owner 仍可使用 `!treesync` 手動同步。被設定停用的模組不會出現在本機命令樹，因此同步時其舊遠端指令會一併移除。2026-10-15 曾依使用者要求把全域入口由 29 精簡為 13；2026-10-19 再依最新要求加入 `/score`，當時共 **14 個**。歷史紀錄見 `reports/command-registration-20261019.json`。Bot 需要 Message Content Intent 支援文字指令；選配管理功能另需其原伺服器權限與設定。

Bot Ready 後會在 log 印出登入帳號，並每 10 秒輪替遊戲狀態文字；第一則會使用 `bot.ini` 設定的預設 prefix。

本次既有 Token 的 Message Content Intent 已通過實際連線，**不需要額外 Windows／管理員權限**。依使用者後續要求，正常 Bot 已保持背景運行供測試；下面的有界連線工具則會在檢查後自行斷線。

## 唯讀連線驗證

```powershell
uv run --no-sync python -X utf8 -m penguin discord-check --env-file .env --gateway
```

這個工具使用暫存資料庫，載入完整預設模組後驗證官方 Bot API 和 Gateway READY，隨即關閉；封鎖發訊、遠端指令更新、使用者事件回呼及玩家來源請求。檢查報告在 `reports/discord-live.json`。可以設定 `TEST_GUILD_ID` 指定要讀取指令清單的伺服器；未設定且 Bot 僅加入一個伺服器時，只讀取該伺服器的指令清單，並不假定它是測試環境。該報告的舊指令數是歷史快照；目前註冊及實際背景程序狀態見 `reports/command-registration-20261019.json` 與 `reports/select-ui-runtime-20261019.json`。

若需要驗證你本人的 Chunirec 讀取，在 `.env` 填入選填的 `CHUNIREC_PLAYER_TOKEN` 與官方支援的 `CHUNIREC_REGION=jp2` 或 `jp`，執行：

```powershell
uv run --no-sync python -X utf8 -m penguin source-check --env-file .env
```

此工具只讀本人 profile／records 並回報筆數，沒有保存成績或綁定 Discord 用戶。缺少 Token 時明確回報 `not_configured` 並以失敗狀態結束；目前即為此狀態。日常玩家綁定使用 `/account` 的「連結帳號」私人表單，不需要把每位玩家憑證放進伺服器 `.env`。

## 關聯式面板

使用 `/menu` 開啟主選單；主資料頁提供帳號狀態與同步／連結，Song 保留單曲脈絡，計算另開面板。一般操作在同一則公開訊息更新，敏感操作私人處理。Poopenguin 指令與既有面板保留。完整路徑見 [面板說明](docs/INTERACTION_PANELS.md)。

B50 會依原歌曲 ID 補齊公開曲繪快取，首次可能需等候下載；後續重用快取。未新增必要環境參數，仍使用既有 `.env`。

## 入口與功能路徑

| 入口 | 行為 |
|---|---|
| `/menu` | 主資料、Song、Poopenguin、計算工具四個分類 |
| `/help` | 指令說明及使用手冊 |
| `/account` | 帳號狀態與動態登入／連結／同步／匯入；紀錄選單開啟 B50、最近遊玩及來源成績，設定另有按鈕 |
| `/chart [query]` | 共用歌曲卡，初始為視譜；八項功能使用選單切換 |
| `/score [query]` | 同一套歌曲卡，初始為個人成績；保留查詢自動完成 |
| `/tools` | Rating、定數、目標 Rating、容錯、餡蜜計算 |
| `/compare [user] [kamaitachi]` | 原版 A：查看最近成績訊息對應的最佳成績；也保留回覆訊息的 `!compare` 與 **View your score** |
| `/permissions` | 原管理者權限入口 |

同步、登入／登出、分享、別名及視譜透過 Embed 操作，詳見 [操作路徑](docs/INTERACTION_PANELS.md)。歌曲卡只留關閉按鈕，選單保留視譜、分數、評價／社群評價、譜面詳情（原概覽）、成績分佈、容錯／失分、別名、找錯歌。其餘單曲入口暫時移除。一般選單及結果公開，憑證、匯入及確認刪除使用私人回應；已保存的分享設定仍受尊重。

Account 來源選單固定 **NET、Chunirec、Chunilib**。Chunirec 選中後再選 jp2／jp；Chunilib 顯示 [首頁](https://chuni.tsaibee.org/) 連結與私人 player-data JSON 上傳。Kamaitachi 不再列入 Account 選單，原比較及後端資料相容保留。

Chunilib idx 與 A ID 屬不同命名空間，匯入來源 ID 保留。尚未對應曲庫的資料可從 **主資料 → 來源成績** 查詢，不會以曲名或相同數值建立永久關聯，也不套用其他來源的定數。

Kamaitachi 帳號綁定需配置 A 原有 client 設定並顯式啟用 `chunithm.kamaitachi`。它不是 Chunirec 或 Chunilib 的替代認證；第一階段不必啟用。

## B 功能與安全設定

預設載入 `/ask`、`/pick`、`/rng`、`/rcg`、`/gacha`、`/copypasta`（文字別名 `cp`）、`/keyword` 及其原有子指令／互動。文字模板與關鍵字從新的空資料開始，抽卡使用原預設卡池；舊資料沒有自動複製或覆寫。

所有非 CHUNITHM 的 B 功能與支援模組集中在 `chuni_penguin/cogs/poopenguin/`，原 `legacy_b` import／設定名稱仍相容；持久資料仍位於 `data/poopenguin/`。全伺服器共用的模板、關鍵字及卡池修改需 Bot owner；查詢與一般使用保持開放。JSON 寫入保留前一版 `.bak` 並原子替換，損壞資料會拒絕覆寫。

下列模組預設關閉，只有管理者明確設定 `[cogs] extra` 後才載入：

```ini
[cogs]
# 範例；請只填入確實要啟用的模組。
extra = poopenguin.messages_cog, poopenguin.vote_cog, poopenguin.admin_cog
```

- `messages_cog`：關鍵字自動回應、重複訊息、指定頻道自動反應。已移除訊息內容日誌，文字指令不會重複觸發。
- `vote_cog`、`admin_cog`：保留 vto／setvote、autoreact／autoban／setperms／lang；管理設定仍需原必要權限。B 原 `!sync` 管理入口已移除，統一使用 A 的 owner `!treesync`，避免與成績同步混淆。
- `feed_cog`：Bluesky 追蹤與自動發訊，設定仍限 Manage Server；沒有預設追蹤帳號。
- `ai_cog`：原 LM Studio 功能。需另設定 `LM_STUDIO_BASE_URL`（只接受 loopback）與 `LM_STUDIO_MODEL`；沒有 OpenAI API 呼叫，未啟用時不傳送訊息給模型。

可供挑選的功能見 [Poopenguin 功能清單](docs/POOPENGUIN_FEATURES.md)；原盤點與處置見 [B 功能對照](docs/B_FEATURES.md)。

## 統計、標籤與後續 C 接點

成績分佈會依所選譜面的可信 Chunirec ID 按需取得公開統計，沿用 C 原 `ChartService`、數值引擎與完整視圖產圖，快取 24 小時。頁面標示來源、實際擷取時間、樣本及模型限制；隨附的 12 份快照僅作有日期標註的離線備用。切換難度保持在分佈頁；缺資料會如實提示。[使用方式與實測](docs/SCORE_DISTRIBUTION.md)。

社群標籤已掛到 834 張實際譜面；兩個額外未編號原列保留來源原文而不啟用，詳見 [資料整合說明](docs/DATA_INTEGRATION.md)。v3 沒有已產生的語義結果，UI 不顯示空白欄位，也不觸發 LLM 補造分析。

後續 C 可讀取 A 原 `personal_bests`、具來源名稱的 `v2_player_sources` 與已審核標籤。新的推薦或玩家能力模型未實作，原 Rating 推薦亦未當作新系統啟用。

## 隱私、備份與驗證

玩家指令及可切換成績的 `/chart` 使用 ephemeral；敏感 guild 文字指令會要求改用 slash／私訊。Chunirec Token 加密後儲存，金鑰位於 `data/.vault.key`；請限制整個 data 目錄的作業系統權限，備份資料庫時另行保存金鑰。A 的 NET Cookie 沿用原本機儲存，仍需相同目錄權限保護。錯誤輸出不含原始 HTTP URL、Token 或 SQL 參數。

沒有遷移或修改舊專案玩家資料。正式資料遷移前必須離線備份、驗證並準備復原，見 [遷移邊界](docs/MIGRATION.md)。

```powershell
uv sync --no-dev --group test --extra rating
uv run --no-sync python -X utf8 -m pytest tests -q
```

驗證結果及尚待操作項目見 [交付報告](DELIVERY.md)；工作紀錄見 [PLAN.md](PLAN.md) 和 [ISSUES.md](ISSUES.md)。
