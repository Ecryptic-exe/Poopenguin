# Song／Account Embed 互動盤點

後續修正：本盤點的原始狀態屬前輪部署；搜尋後來將同歌一般／WE 合成一個候選，特殊版本收進難度選單，ID 只作無法區分候選時的備援。`gengaozo` 已從四筆提示改成一張卡；最新描述以 docs/EMBED_UX_PROPOSAL.md 與 PLAN.md 為準。


盤點日期：2026-10-16（Asia/Taipei）  
範圍：目前程式碼中的 Song 與 Account 互動面板、嵌入訊息、表單、按鈕、選單及其條件。這份文件是源碼盤點，不是已核准的全面重設稿。

狀態標記：

- **[現況]**：源碼目前可見的行為；不代表已部署。
- **[本輪已部署供測試；待 Discord 真人驗收]**：本輪新增或調整已部署；完整視覺流程仍待使用者在 Discord 驗收。
- **[提案]**：建議方向，尚未實作。

命令名稱辨識：目前統一面板的主要入口是 `/account`、`/chart`、`/tools` 與 `/compare`；面板內的 `account_settings`、`run_sync`、`find`、`tags`、`alias_add` 等名稱是 UI route／callback key，不代表使用者可直接輸入同名 slash command。`entrypoints.py` 會把部分舊 app command 收進 callback registry，再用統一入口取代；下文只在確認仍註冊的入口前加 `/`。`/compare` 目前仍指舊的近期成績訊息比較；Song More 的單曲比較則是另一個面板 route。

本輪狀態：本機完整回歸 591 項通過；21:50 重啟後 21 個模組、Gateway 連線及活程序身分驗證通過，stderr 為 0 error。隔離啟動有 0 次網路呼叫、0 個待處理工作；副本新增 3 張評價表，原 28 張表 hash 不變，2288 首歌曲、8312 張譜面及 834 筆 annotations 不變。已部署供測試，新增評價表為空；部署記錄見 `reports/embed-features-runtime-20261016.json`、`reports/review-migration-live-20261016.json`，待使用者做 Discord 真人驗收。

## 入口與共用面板

| 層級 | 嵌入內容 | 按鈕與選單 | 動態顯示與條件 | 狀態保留／注意事項 |
|---|---|---|---|---|
| `/account` | 主資料介面：五個來源連結狀態、目前來源、最後同步時間；可能帶有同步／連結結果訊息。 | 來源選單；「同步資料」「連結帳號」；個人資料分類選單。 | 來源狀態依 NET cookie、Kamaitachi token、Chunirec 憑證或 Chunilib 匯入資料判定。 | 回應以 `thinking=True` defer，沒有指定 ephemeral；後續主資料嵌入沿用回應的公開／私密狀態。源碼 `ui/entrypoints.py:42-55`、`ui/panel.py:768-825`。**隱私風險：**公開頻道可見個人來源、同步時間與嵌入內容；「只有你能操作」只限制操作人，並不隱藏訊息。 |
| `/chart`（不帶查詢） | Song／譜面入口說明；同一訊息後續更新。 | 「搜尋歌曲」開啟查詢表單；歌曲分類選單。 | query 可用歌曲自動完成。 | `/chart` 入口 defer 也未指定 ephemeral。源碼 `ui/entrypoints.py:58-80`。 |
| `/chart query` | 選歌結果或單曲資訊卡。 | 歧義搜尋時有候選下拉、首選捷徑及重新輸入；單曲卡可切分頁。 | 精確且明確的結果可直接開卡；近似、短查詢或同名多結果會要求確認。 | 公開／私密狀態沿用 `/chart` 原始回應。Song 卡僅限制誰能操作，不會令公開訊息變私密。搜尋分流在 `ui/panel.py:827-848`、`ui/song_search.py`、`services/song_discovery.py:56-134`。 |
| 共用面板導覽 | 主頁顯示 Song、Account、Poopenguin、計算工具四個入口；各分類頁顯示分類內功能說明。 | 分類頁以一個下拉選單導覽；Account 分類可到主頁、B50、最近遊玩、來源成績、帳號設定、匯入；Song 分類可到歌曲、譜面篩選、人工標籤。 | 同分類選項依所在分類變化；娛樂分類另可顯示「返回上一層」。 | `PanelSession` 保留 owner、route、查詢、來源、難度、歷史（最多 8 筆）、表單參數、操作鎖與當前訊息；多數路由透過同一訊息更新。面板和 modal timeout 分別約 600／300 秒。源碼 `ui/panel.py:19-150`、`154-215`、`426-472`。**缺口：**Song／Account 分類沒有明確的全域主頁或返回按鈕；`home` 在分類內會轉成該分類首頁，跨分類操作會被拒絕。 |
| 共用按鈕／ID | 面板按鈕以 `penguin:panel:{action}:{row}`、來源選單以 `penguin:panel:source`、比較玩家選單以 `penguin:panel:compare_user` 綁定。 | 面板視圖按所在路由動態建立按鈕。 | owner ID 不符、操作忙碌或面板過期時顯示私密提示或重新搜尋提示。 | ID 穩定，session 負責同訊息路由狀態；Song 卡以 `song_details:*` custom ID 保持控制辨識。源碼 `ui/panel.py:533-655`、`ui/song_details.py:91-207`。 |
| 延遲處理狀態 | 約 0.6 秒仍未完成時，短暫以「⏳ {項目}處理中…」替換訊息，再由完成結果覆蓋。 | 無額外按鈕；處理期間通常暫時移除舊 view。 | 路由、Song 首次載入與切分頁使用不同 wrapper；匯入、Chunirec 綁定仍走各自 callback。 | **[本輪已部署供測試；待 Discord 真人驗收]** `ui/processing.py`、`ui/entrypoints.py:42-70`、`ui/panel.py:1236-1245`、`ui/song_details.py:649-707` 已有延遲提示與 revision／lock 防舊結果覆寫。Panel 標籤依路由名稱取值，`run_sync`、alias 表單等可能只顯示通用「資料處理中」。 |

## Account 與來源資料樹

| 層級 | 嵌入內容 | 按鈕與選單 | 動態顯示與條件 | 狀態保留／注意事項 |
|---|---|---|---|---|
| 主資料介面 | 五個來源逐項顯示「已連結／已匯入／未連結」；顯示目前來源及最後同步時間。 | 來源下拉：CHUNITHM-NET、Kamaitachi、Chunirec jp2、Chunirec jp、Chunilib 匯出檔。按鈕：「同步資料」「連結帳號」；分類選單。 | 「連結帳號」對 NET 開登入流程；對 Chunirec 開 Token 表單；Kamaitachi 提示管理員先配置原有整合，再使用既有綁定 route；Chunilib 提示從選單上傳。 | 目前來源與查詢／難度保存在 PanelRoute；切換來源會重新顯示目前頁。源碼 `ui/panel.py:36-41`、`581-655`、`768-825`、`1122-1150`。**隱私風險同入口表。** |
| 同步資料 | 同步結果列出來源與同步筆數；匯入來源另列已對應／未對應筆數，並提示未對應資料可從 Song 成績頁查詢。 | 主資料介面「同步資料」直接執行；來源選單切換來源；Chunilib 需走匯入選單。 | NET／Kamaitachi 讀基本資料、最近遊玩與個人最佳；Chunirec 用已存 Token 同步；Chunilib 顯示上傳指示。NET 未登入時最佳／最近路由返回登入提示。 | 源碼 `ui/panel.py:885-987`、`cogs/v2_players.py:183-190, 363-390`。在同步操作結束後會切到「同步資料」說明頁；該頁目前只有來源選單，沒有再次顯示「立即同步」按鈕。 |
| NET 連結／登入 | 三頁登入教學：在私人視窗登入 NET、開指定連結、執行 bookmarklet；最後一步依伺服器設定顯示 passcode／server 或 DM 指示。 | 分頁控制；第 1 頁有 SEGA ID 登入；點擊後先出私密警告，再按鈕開登入表單。表單含 username、password、選填 6 位雙重驗證碼。 | SEGA ID 按鈕只在教學第 1 頁顯示；登入流程最多等待 300 秒，最後同訊息顯示成功、失敗或逾時。 | 面板登入在等待時可由其他操作取消 pending task；token 不在面板直接輸入。源碼 `ui/login.py:23-157,159-255`、`cogs/chunithm/auth.py:153-338`、`ui/panel.py:271-293`。教學主要為英文。 |
| Chunirec 連結 | 私密 Token 表單；提交後同步，顯示已匯入、已對應／未對應數。 | 一個必要 Token 欄位（10–200 字）；地區由來源選單決定為 jp2 或 jp。 | 只有自己可提交；成功／失敗後回到主資料介面。Token submission 會清除欄位與互動 payload 內容。 | 源碼 `cogs/v2_players.py:32-118`、`ui/panel.py:1134-1150`。舊 `account link` callback 也保留 region 選項，但目前統一 `/account` 入口沒有公開 `link` 子命令。 |
| Kamaitachi 連結 | 提示需先配置原有模組；面板沒有內嵌 Token 表單。 | 帳號入口的「連結帳號」按鈕仍可點；後續由既有 Kamaitachi callback 處理。 | 未配置時回報不可用；解除連結需使用 Kamaitachi 自己的帳號管理流程。 | `ui/panel.py:1122-1132`、`cogs/chunithm/kamaitachi.py:59-94,198-214`。這裡的 route 名稱不等同已確認可見的 slash 子命令。 |
| Chunilib 上傳／匯入 | 私密 File Upload 表單，標示上傳自己的 `player-data JSON`、最大 5 MiB。完成後顯示匯入、映射筆數與資料用途。 | 一個檔案上傳元件，最少／最多各 1 個；回主資料介面。 | 超過 5 MiB、格式錯誤或權限檢查失敗會回傳錯誤訊息；資料本身由來源快照匯入，不建立未確認 ID 對應。 | 源碼 `ui/panel.py:1119-1121,1276-1330`、`services/player_sources.py`。此 callback 目前沒有共用延遲處理 embed。 |
| 帳號設定 | 顯示成績分享狀態：僅本人可查詢／允許其他玩家查詢與比較；提示解除連結和刪除會再確認。 | 「切換成績分享」「登出 NET」「解除目前來源」「刪除全部本機資料」「查看 NET 憑證」。分類選單、來源選單。 | 目前五個按鈕無來源條件；NET logout、unlink、delete 都會開確認視圖。Token action 需 NET cookie 存在，否則 `token_value` 會回錯誤。 | 源碼 `ui/panel.py:581-655,715-767,1151-1185`、`cogs/chunithm/auth.py:350-357`。unlink 只支援 Chunirec jp2／jp、Chunilib；NET 應登出，Kamaitachi 用原管理功能。按鈕未按來源隱藏，可能形成錯誤／死路。 |
| 分享隱私 | 只顯示目前 `privacy_mode` 狀態；切換按鈕直接反轉並重畫設定頁。 | Account 設定 toggle；選配 `profile_settings` 模組另有 config privacy 定義，目前未啟用。舊 `privacy` callback 不代表公開 slash 入口。 | 沒有 UserConfig 時預設 `privacy_mode=False`，即允許其他人查詢／比較。 | 源碼 `ui/panel.py:715-727,939-946`、`database/users.py:33`、`cogs/chunithm/profile_settings.py:20-124`、`cogs/v2_players.py:276-285`。**共用設定：**Panel toggle、舊 privacy callback 與未啟用的 config privacy 定義都操作同一欄位；目前不把選配定義當成已註冊入口。 |
| 登出 NET | 確認卡說明登出自己的 NET 帳號；操作清除 Bot 保存的 cookie／本機憑證，不會刪除來源網站帳號。 | 「確認操作」／「取消」。 | 有進行中的成績查詢時，舊 auth 路徑會防止登出或先等待；確認結果回主資料介面。 | `ui/panel.py:1151-1167`、`cogs/chunithm/auth.py:37-91`。`invalidate` 不由面板選項提供；不等於登出 SEGA 網站帳號。 |
| 解除來源 | 確認提示為解除目前來源並刪除該來源本機匯入資料。 | 「確認操作」／「取消」。 | 實際只可解除 Chunirec jp2／jp、Chunilib export；其他來源會由 `SourceError` 拒絕。 | `ui/panel.py:963-967,1151-1167`、`services/player_sources.py:303+`。設定頁的無條件「解除目前來源」可能讓 NET／Kamaitachi 使用者走到拒絕訊息。 |
| 刪除全部本機資料 | 確認卡說明刪除本機帳號、成績、偏好；來源網站資料不受影響。完成後提示備份檔須依保存政策由管理員清理。 | 「確認操作」／「取消」。 | 若來源鎖定或仍有進行中的帳號查詢會拒絕刪除；確認後回主資料介面。 | `ui/panel.py:968-970,1151-1167`、`cogs/v2_players.py:245-274`。刪除包含 PlayerSource、PB、Cookie、UserConfig、PendingKamaitachiImport，也刪除新評價的 reviewer profile（透過 FK cascade 清除其個人評價／歷史）。 |
| 查看 NET 憑證 | 以 spoiler 包住 NET 憑證並附勿分享文字。 | 單一按鈕，沒有額外確認。 | 無有效 cookie 時 token 讀取回錯誤；按鈕仍固定顯示。 | `ui/panel.py:958-962,329-339`、`cogs/chunithm/auth.py:350-357`。此 action 用 sensitive context；若原面板公開，會另開私密回應。主資料介面的來源狀態仍可能留在原公開訊息。 |
| Best 50 | NET／Kamaitachi 使用既有 B50 圖片／資料卡；匯入來源依可信 source constant 在本機排序，最多 50 筆、每 10 筆一頁，註明不是遊戲內 Rating 組成。 | 來源選單；「重新整理」；匯入來源可翻頁。 | NET 未登入會提示登入；匯入來源沒有可信定數時錯誤並引導到來源成績查詢。 | `ui/panel.py:581-655,885-935`、`cogs/chunithm/best_plays.py`。不同來源的呈現形式不同；NET／Kamai 延用舊 renderer，匯出來源顯示簡化文字清單。 |
| 最近遊玩 | 每頁一局 credit 的多張成績卡；可按局次翻頁。 | 分頁按鈕；「View details of…」下拉，選擇該局其中一筆來載入詳細成績；來源選單限 NET／Kamaitachi；面板另有「重新整理」。 | 詳細資料是否抓取取決於來源是否支援；Chunilib／Chunirec 不提供可驗證遊玩歷史，面板拒絕把 PB 更新時間當最近遊玩。 | `ui/recent.py:25-128`、`ui/panel.py:546-560,885-935`。下拉選項會隨目前 credit 頁切換。 |
| 來源成績 | 依來源查詢自己的已記錄成績，未映射的匯入列顯示標題、難度、分數。 | 五來源選單；「查詢此來源成績」開 query modal；結果可翻頁。 | NET／Kamai 以曲庫歌名找 ID；匯入來源以標題包含查詢文字或完全相同 source ID 篩選，不建立對應。 | `ui/panel.py:581-655,850-884`、`cogs/v2_players.py:313-365`。與 Song「我的成績」及 B50／最近遊玩重疊。 |
| 成績比較 | Song More 的 panel compare route 可選來源、歌曲及一名 Discord 玩家；結果只列雙方共通歌曲並計分差。 | 來源選單；「選擇歌曲」modal；User Select 選玩家；分頁。 | 避免比較被設為隱私的對象；沒有共同譜面時提示。 | `ui/panel.py:408-432,581-676,1019-1067,1072-1099`。`group_for("compare")` 預設歸入 Song，因此從 Song More 進入時不會被跨分類 guard 拒絕；目前 compare route 不在 Account 分類選單。公開 `/compare` 是另一條舊流程，依近期成績訊息比較。 |

## Song 搜尋、資訊卡與分析樹

| 層級 | 嵌入內容 | 按鈕與選單 | 動態顯示與條件 | 狀態保留／注意事項 |
|---|---|---|---|---|
| 歌名搜尋／候選 | 「你要找的是哪一首？」卡片，列出搜尋字串及最多 10 個候選標題／藝人／版本／Song ID。 | 最多 25 個項目的候選下拉（標題；描述包含穩定 Song ID、藝人及版本）；「就是第一首」；「重新輸入」。 | `SongDiscoveryService` 正規化 NFKC、忽略大小寫／多餘空白；以別名、正式標題、藝人與縮寫打分。空字串或超過 100 字拒絕；候選分數低於 45 無結果。非精確、低信心或前兩名差距小於 10 時要求選擇。 | **[本輪已部署供測試；待 Discord 真人驗收]** 候選清單與 select description 均帶穩定 Song ID，可區分同名且同藝人的候選。候選卡可選擇、採用首選或重新輸入。源碼 `ui/song_search.py:11-97`、`services/song_discovery.py:14-134`。 |
| 找錯歌／重搜 | 重新比對原本 query，並排除目前已選 Song；有候選就重新顯示候選卡，無候選則返回歌曲搜尋提示。 | 單曲卡固定顯示「找錯歌／重搜」。 | 使用 `ignore_exact=True`，跳過原始精確 alias，並排除目前 song ID；使用者可再選不同候選。 | **[本輪已部署供測試；待 Discord 真人驗收]** query 留在 `SongDetailsView.search_query`；來源選擇／難度保留在 panel session。進行重搜前會 retire 舊 view，避免晚到的舊回應覆蓋新結果。若排除後無候選，仍可重新輸入。`ui/song_details.py:729-740`、`ui/song_search.py:97-115`。 |
| Alias 提議 | 成功開卡後，額外私密 follow-up 詢問是否將所用簡寫記為目前伺服器可共用的別名。 | 「加入這個別名」「換個別名」「略過」；換別名開一欄文字 modal。 | 不會為藝人名稱匹配提出 alias；已存在同一對應時不顯示；文字限制 1–100 字且排除 mention／控制字元；別名衝突或正式曲名衝突時提示並禁止覆寫。 | **[本輪已部署供測試；待 Discord 真人驗收]** Alias service 綁定目前 guild ID，因此同伺服器可共用，其他伺服器不共用；內部記錄 owner ID 供移除權限。Alias offer 為私密 follow-up，不替換已開啟的 Song 卡。源碼 `ui/song_search.py:116-295`、`services/alias_learning.py:82-217`。 |
| 舊歌名搜尋 fallback | `bot.utils.aliases` 不可用時，回到舊 fuzzy `find_songs` 流程。 | 舊版單一「Yes」確認；沒有明確取消／重新搜尋按鈕。 | 使用 RapidFuzz QRatio，低於 65 顯示「Did you mean…?」；點 Yes 接受第一候選；15 秒 timeout 默認取消。 | 舊 fallback 在 `context.py:314-338`、`services/catalog.py:454-513`、`ui/confirmation.py:8-28`。需確認正式執行環境是否仍可能走此分支。 |
| 同名歌曲碰撞 | 多筆相同 canonical title 或多個等分候選時顯示候選卡，而非直接以同名結果當唯一命中。 | 搜尋候選下拉；進入單曲卡後若卡內仍有多筆 `Song`，可「上一首／下一首」，中央顯示歌曲 x/n。 | SongDetails 將歌名匹配的多個 Song 保留為 sequence；候選 select description 帶穩定 Song ID、藝人與版本；若是舊 fallback 則只用順序上一首／下一首。 | **現況／剩餘限制：**新候選可用 Song ID 區分同名候選；舊 fallback 或單曲卡內多首結果仍使用序號切換。搜尋候選可重新輸入，單曲卡可重搜。 |
| 單曲卡共用列 | Embed 依 tab 顯示單曲內容；footer 顯示歌曲 x/n、分頁名稱、頁次。 | 固定「概覽」「譜面詳情」「我的成績」「玩家評價」常用 tabs；「找錯歌／重搜」；「更多單曲資訊」select；難度 select。多首命中時上一首／位置／下一首；多頁 embed 時上一頁／下一頁。 | 可用 tabs 及 More 選項依選中 Chart 的資料決定；沒有譜面資料時難度下拉停用並顯示「沒有譜面資料」。 | **[本輪已部署供測試；待 Discord 真人驗收]** 面板由同一訊息更新。保留 song index、tab、difficulty、network、page、mirror、review group。處理提示後由 revision lock 防舊請求覆寫較新頁面。`ui/song_details.py:49-73,554-718`。 |
| 概覽 | Song 標題、別名、曲師、分類、版本／上線日期；各難度與等級／定數；BPM／長度；移除歌曲提示；視譜或 YouTube 搜尋連結。 | 沒有額外欄位按鈕；透過共用列切 tab／難度／更多。 | 別名列依 Alias 資料；下架／無法取得資訊會顯示提示；歌曲有多 Chart 時難度選單列各 difficulty、level、const。 | `ui/song_info.py:19-169`、`ui/song_details.py:260-297`。 |
| 譜面詳情 | Artist、NOTES DESIGNER、版本、總物量及 TAP／HOLD／SLIDE／AIR／FLICK 數量；可能加社群譜面資料與語義摘要。 | 共用 tab、難度、分頁控制。 | Poopenguin annotations 有資料時列 tag、SSS 星數、文字、最多 3 個連結、來源／配對方法；另可列語義資料。 | `ui/song_details.py:299-343`。來源註記包括 Poopenguin sheet／列號；無社群資料時只顯示原始譜面資訊。 |
| 玩家評價（私密入口） | ephemeral 私密面板顯示本人的既有評論／草稿背景；說明公開匿名、評論與背景明細本人可見。 | 「填寫／確認玩家背景」；已存在評價時可「刪除此張評價」；確認背景後出現「繼續填寫譜面評價」；另有關閉、刪除確認／取消。 | 入口只允許自己的 Song panel 作者；刪除只針對該玩家該譜面。 | **[本輪已部署供測試；待 Discord 真人驗收]** 首先 defer ephemeral，再改為私密面板；不存在的 own review 時移除刪除按鈕；尚未確認背景時移除繼續按鈕。`ui/chart_reviews.py:66-168,352-386`。 |
| 玩家背景表單（第 1 步） | 私密 Modal，標題「1／2 確認本次玩家背景」。 | 必填下拉：Rating 區間、主力等級、遊玩經驗。選填多選：擅長項目、不擅長項目；技能項目含鍵盤、交互、縱連、滑鍵、擦鍵、Air、體力、讀譜。 | 新用戶空白；舊用戶沿用已存值；確認後先成為本次草稿，按「繼續填寫譜面評價」才進第二步，尚未提交前不保存。 | **[本輪已部署供測試；待 Discord 真人驗收]** 必填欄位錯誤會提示重新選；`services/chart_reviews.py:16-66` 定義可選值。 |
| 譜面評價表單（第 2 步） | 私密 Modal，標題「2／2 評價這張譜面」。 | 五欄：喜好 1–5、相對目前定數體感（−2 至 +2）、遊玩次數（初次／2–10／11 次以上）、主要目標（通關／SS／SSS／SSS+／FC／AJ／理論值）、必填自由評論 1–1000 字。 | 編輯時預填個人舊值；提交前確認必填欄位及 expected revision，避免不同視窗相互覆寫；成功後以私密訊息回報版本號。 | **[本輪已部署供測試；待 Discord 真人驗收]** 自由評論只存本人紀錄，永不由公開摘要讀取；資料庫以 Discord ID 做私有 owner key，對外卡片匿名。`ui/chart_reviews.py:267-350`、`services/chart_reviews.py:68-210`、`database/chart_reviews.py:17-73`。 |
| 我的成績 | ScoreCard 顯示所選單曲／難度自己的個人成績；沒有資料時提示。 | 來源 select；「目前綁定的帳號」、CHUNITHM-NET、Kamaitachi、Chunirec jp2／jp、Chunilib 匯出；「查詢另一首」由面板 route 提供。 | Native source 查詢 canonical Song；匯入來源按該 snapshot 的成績。未登入或來源不支援時出提示；僅有映射 Song／Chart 時能轉成 ScoreCard。 | `ui/song_details.py:171-207,260-...`、`services/song_details.py`、`ui/components/score_card_embed.py`。結果可能含個人帳號／分數；若從公開 `/chart` 開啟仍是公開內容。 |
| 排行榜 | 依所選難度顯示排名、玩家名稱、分數、clear／combo lamp、判定數、AJC 與時間。 | 來源 select 限 CHUNITHM-NET、Kamaitachi；分頁。 | 無資料時顯示此來源沒有排行榜；登入／網路錯誤用單頁提示代替，保留難度選項。 | `ui/song_details.py:171-207,389-442`。只在 More 選單，不在固定 tab 列。 |
| 統計分析 | 顯示來源樣本分數分布／圖表、樣本數與來源資訊，提示抽樣偏差與不能代表個人實力。 | More 選單中選擇；變更 difficulty 更新圖表。 | 只有 Phase1Charts 對該 Chart 有統計快照時提供。 | `ui/song_details.py:260-...`、`services/phase1_charts.py`。與未啟用的 rating_chart 選配模組統計來源不同。 |
| 選配 command 定義 `song_search`（目前未啟用） | 若啟用，私密文字結果最多 10 行，列歌曲、可用難度與 `id:`。 | 只有 query 輸入；無互動選單或「開啟 Song 卡」按鈕。 | 沒有符合結果時回「沒有找到符合的曲目」。 | 程式定義在 `cogs/chunithm/rating_search.py:17-34`，但目前 startup 將 `chuni_penguin.cogs.chunithm.rating_search` 標記 disabled；13 個已載入 slash root 沒有此入口。啟用需載入此 cog 並同步 command tree；目前不是已註冊入口。 |
| 選配 command 定義 `rating_chart`（目前未啟用） | 若啟用，產生所選歌曲／難度的分布圖與摘要；footer 說明資料來源／版本及關聯限制。 | 歌曲 autocomplete、difficulty（預設 MASTER）、詳細開關；搜尋不唯一時出最多 25 首下拉（label title、描述 difficulty＋ID）。 | 唯一精確命中直接產圖；命中其他難度時列可用難度；無候選提示；多候選需選擇。候選選擇會先顯示「正在處理…」再產圖。 | 程式定義在 `cogs/chunithm/rating_analysis.py:21-96`、`ui/rating_chart.py:9-115`，但目前 startup 將 `chuni_penguin.cogs.chunithm.rating_analysis` 標記 disabled；13 個已載入 slash root 沒有此入口。啟用需載入此 cog 並同步 command tree；目前不是已註冊入口。 |
| Rating 試算 | 目標分數 S、S+、SS、SS+、SSS、SSS+、MAX 對應單曲 Rating。 | More select 選擇。 | 常數可用且難度非 WE 才提供；WE 或未知定數顯示原因。 | `ui/song_details.py:390-430`。 |
| 容錯與失分 | 使用總物量顯示目標分數的 JUSTICE／ATTACK／MISS 容錯組合。 | More select 選擇。 | 只有 `maxcombo` 有值才開放；沒有總物量時顯示不可計算提示。 | `ui/song_details.py:390-430`。 |
| 視譜 | 顯示 sdvx.in 視譜圖片與來源連結；可切 MIRROR。 | More select 選擇；視譜 tab 出現「MIRROR」切換按鈕。 | 只對有 `sdvxin_chart_view` 的所選 Chart 提供；否則回到相關連結提示。 | `ui/song_details.py:443-...`、`554-603`。 |
| 相關連結 | 歌曲 Wiki、YouTube 搜尋；條件式 sdvx.in 視譜與曲繪 URL。 | More select 選擇；embed 內為連結。 | sdvx.in link 需該 Chart 有 view mapping；曲繪連結需 jacket URL。 | `ui/song_details.py:260-298`。 |
| 曲繪 | 概覽會顯示曲繪縮圖；相關連結可附「開啟曲繪」。 | 目前沒有可選的獨立曲繪 tab；More select 刻意不列 `jacket`。 | 曲繪 URL 有值時才提供圖片與連結。 | `render()` 雖保留 jacket 分支，但目前沒有 UI 導航到該 tab；曲繪本身仍可從概覽／相關連結開啟，不是缺少曲繪功能。`ui/song_details.py:135-158,260-298`。 |
| 譜面篩選（Panel route `find`） | 輸入等級或定數範圍，選填難度、版本、標籤；結果每頁列約 15 個譜面與外部連結。 | Panel modal 欄位；結果分頁控制。 | 精確等級／定數 range、difficulty、version、Phase1 tags 組合過濾。 | `ui/panel.py:43-53,678-714,857-884`、`cogs/chunithm/search.py`。`find` 是統一面板內部 route；舊 command 已收作 callback，不應寫成目前公開 slash 入口。結果清單沒有按鈕回單曲卡。 |
| 人工標籤（Panel route `tags`） | 依輸入配置標籤列出歌曲、難度與 tags；每頁 8 列，footer 標註 Poopenguin 社群資料。 | 文字 modal（「配置標籤」）；分頁。 | 無匹配時回歌曲面板並提示。 | `ui/panel.py:988-1018`。`tags` 是面板 route/callback 名稱，不是目前統一面板的 slash 入口；清單沒有開啟對應 Song 卡的按鈕。 |
| 別名清單／管理 | Song More ->「這首歌曲的別名」執行舊 `alias list` callback；後續面板可顯示新增、移除、重新載入按鈕。 | 新增：歌曲／別名欄位、可選 global true/false；移除：一個 alias 欄位；reload 無輸入。 | 搜尋後的自動 alias 提議為目前伺服器共用；舊管理 callback 可建立 global alias，但僅 alias manager／owner。新增衝突會拒絕覆寫；reload 要求 owner 權限。 | **[本輪已部署供測試；待 Discord 真人驗收]** 使用者已選定新 alias 為同伺服器共用。Learned alias 以 guild ID 儲存；搜尋成功開卡後私密詢問，確認前不寫入，衝突不覆寫。舊 alias 管理面板與一般搜尋提議是兩條流程。`ui/song_search.py:141-295`、`services/alias_learning.py:82-217`、`cogs/chunithm/aliases.py:74-286`。 |

## 重複入口、卡住處與明確提案

| 類型 | 發現 | 影響／處理建議 |
|---|---|---|
| 隱私 | `/account` 與 `/chart` 入口預設公開 defer；主面板沿同一則訊息更新；owner check 只限制操作人，不會隱藏訊息。 | **[提案]** Account／Song 主面板維持公開及同訊息更新；只將憑證、表單、別名提醒與評價管理入口做成私密互動。嵌入不得顯示 token／cookie；個人成績沿主面板原可見性，不因切選單額外改成 private。 |
| 導覽 | 分類選單只列同類功能，`home` 在分類中會落回該分類首頁；Song More 與 Account 是獨立功能群。 | **[提案]** Account 固定只管理來源、同步、連結與個人紀錄；Song 固定只操作當前歌曲與難度。面板內的 route key 不要誤寫成 slash command。 |
| 比較路由 | Song More 的單曲比較目前會進 panel compare route 並沿用單曲／難度／來源；它不在 Account 分類選單。公開 `/compare` 是近期訊息比較。 | **[提案]** 讓兩種比較標籤明確區分用途；Song More 保留「比較這首歌曲」，Account 不重複放置該入口。 |
| 多來源帳號設定 | 「解除目前來源」和「查看 NET 憑證」無條件顯示；前者只支援 Chunirec／Chunilib，後者需要有效 NET cookie。 | **[提案]** 僅在支援來源／已綁定狀態顯示；對不支援狀態改成引導操作說明。 |
| 重複隱私設定 | Panel toggle 與舊 privacy callback 共用設定；選配 profile_settings 的 config privacy 定義目前未啟用，不能當成現有第三個入口。 | **[提案]** 保留一個設定入口、使用明確「允許其他人查詢」文字，顯示目前預設狀態。 |
| 重複成績面 | 個人成績可從 Song 詳情、Account 的來源成績、Best 50、最近遊玩及舊 score callback 查詢。 | **[提案]** Account 聚合個人來源狀態與歷史；Song 卡只顯示目前歌曲／難度的單曲成績，保留來源切換但不複製歷史入口。 |
| 重複 Rating 搜尋 | Song 卡統計分析、目前未啟用的 `rating_chart`／`song_search` command 定義與舊 rating callback 各有不同資料與目的。 | **[提案]** 在介面上清楚分成「單曲分數試算」「社群樣本統計」「歌曲／難度搜尋」，標示來源，不混為同一頁；先啟用並同步對應 cog 才能把兩個 command 列作正式入口。 |
| Find／Tags | 篩選與標籤結果僅列清單，不能直接打開 Song Detail。 | **[提案]** 每列提供「打開歌曲／難度」互動；保留篩選 query 作回到清單時的上下文。 |
| Alias 權限 | 新學到的伺服器 alias 和管理員 global alias 共存；手動 alias 表單仍讓人輸入 global true/false。 | **[提案]** 一般使用者只需確認「本伺服器共用」；global alias 從一般表單移除並留給管理員工具；清楚顯示衝突歌名。 |
| 同名歌辨識 | 多候選有藝人與版本描述，但沒有歌曲 ID／各難度摘要；同標題、同藝人、同版本可能仍看不出差異。舊 fallback 則只有上一首／下一首。 | **[提案]** 候選卡顯示版本、可用難度／定數與穩定 ID；把候選直接選擇與錯歌重搜保留在同一訊息。 |
| 玩家評價背景維度 | 背景表單有擅長／弱項、熟悉次數、目標，但目前公開彙總只按 Rating／主力等級／遊玩經驗分組。 | **[提案]** 先明確哪些背景欄位只用於私下分層、哪些可公開分組；所有公開群組維持 n≥5 與小補集抑制，不公開自由評論及個人列。 |
| 布局範圍 | 目前為 `discord.ui.View` + embed 的 route/session 面板，不是全面 LayoutView 新版 layout。 | **[提案]** Song／Account 全面 layout 改版另立設計決策；本輪只據現況盤點和明確標示新功能狀態，不把提案寫成已落地。 |

## 主要來源索引

| 主題 | 源碼 |
|---|---|
| 入口及延遲處理 | `chuni_penguin/ui/entrypoints.py:8-111`、`chuni_penguin/ui/processing.py:11-114` |
| 共用面板、狀態與導覽 | `chuni_penguin/ui/panel.py:19-150,154-215,393-655,715-1275` |
| Account／來源／匯入 | `chuni_penguin/cogs/v2_players.py:32-118,183-285,313-390`、`chuni_penguin/services/player_sources.py` |
| NET 登入 | `chuni_penguin/ui/login.py:23-255`、`chuni_penguin/cogs/chunithm/auth.py:37-91,153-357` |
| Song 卡 | `chuni_penguin/ui/song_details.py:49-740`、`chuni_penguin/ui/song_info.py:19-169` |
| 搜尋與 alias | `chuni_penguin/ui/song_search.py:1-295`、`chuni_penguin/services/song_discovery.py:14-134`、`chuni_penguin/services/alias_learning.py:82-217`、`chuni_penguin/cogs/chunithm/aliases.py:82-286` |
| 匿名譜面評價 | `chuni_penguin/ui/chart_reviews.py:24-386`、`chuni_penguin/services/chart_reviews.py:16-258`、`chuni_penguin/database/chart_reviews.py:17-73` |
| 評分搜尋／成績卡與分頁 | `chuni_penguin/cogs/chunithm/rating_analysis.py`、`chuni_penguin/cogs/chunithm/rating_search.py`、`chuni_penguin/ui/rating_chart.py`、`chuni_penguin/ui/_pagination.py` |

