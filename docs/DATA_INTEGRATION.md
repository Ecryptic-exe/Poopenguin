# 資料關聯與人工審核

A 的 Song／Chart 與 seed 為唯一 canonical 曲庫。2026-10-16 已將既有社群譜面資料搬入獨立資料表；原曲庫與本機既有玩家資料保持原樣。

- Chunirec 個人成績：官方 owner Token GET，略過 user_id／user_name，僅讀 Token 持有者。依 A 的 `Song.chunirec_id` ＋難度唯一對應；找不到或有多重對應則保留來源快照，不能以曲名補猜。
- Chunilib 匯出：經驗證及欄位白名單清理，保留 `chunilib:export` ID；不把 idx 直接當成 A song_id。不匯入未知 JSON 欄位、Cookie 或 Token。
- C 統計：所選譜面以可信 Chunirec ID 按需取得公開資料，保留來源／時間／輸入雜湊，驗證後沿用既有 C 模型產圖，快取 24 小時。`snapshots/statistics/index.json` 的十二張已審核輸入只作標明日期的備用，不再當成整體可用範圍。詳見 [成績分佈](SCORE_DISTRIBUTION.md)。
- B 社群資料：`chart_annotations` 保存 834 筆已對應譜面資料，`chart_annotation_tags` 保存 765 個標籤關聯。462 筆有標籤、600 筆有心得、103 個可點連結；SSS 星數、來源定數／物量、旗標、A:J 原值、富文字與原始連結一併保留。來源值不覆寫 canonical 譜面。826 筆自動精確配對，8 筆由 Luna 核對，方法與證據分開保存，不標為人類作者審核。
- LLM：只呈現已保存且明確審核的描述及來源。原 v3 的 semantics 表實際有零列；目前 `semantic_bindings.json` 留空，不呼叫 LLM 補造內容。

## 社群資料庫與更新

2026-10-16 正式匯入已完成。Song 的「譜面詳情」、人工標籤瀏覽及篩選譜面共用資料庫查詢，不再由 `manual.json`／`manual_bindings.json` 決定正式顯示內容。JSON／XLSX 保留為來源證據；原先的 815 列候選報告是歷史資料，不代表目前的關聯數量。

- 匯入來源是 `outputs/chart-mapping-20261015/luna/ChunithmChart_reviewed.xlsx`，對照 `bm25-matches.json`、Luna `review.json`、原始 JSON 及前版 XLSX。
- 每筆必須符合實際 chart_id、song_id、difficulty；重複列、錯誤編號、內容／雜湊不一致會整批拒絕。相同版本重跑不重複新增。
- 五張資料頁另有 `14!335` Imprinting 與 `15以上!38` PhenomenoN 兩個未編號原列。完整原文存在 `source_snapshots` 的排除列紀錄；不推測 ID、不當成可查詢譜面啟用。PhenomenoN 的 15.0 原列與已對應的 14.9 原列分開保留。
- 來源作者及原撰寫日期沒有可靠資料，保留未知；匯入時間不冒充撰寫時間。星數與心得是來源意見，不改動官方曲庫值。
- 更新前先停 bot 並做 SQLite 一致性備份，重新產生相符的來源／對照／核對檔，再使用 `import-annotations`。預設只檢查，加 `--apply` 才寫入；失敗整批回滾。
- 若要停用此批資料，用 `rollback-annotations --batch-id`；保留匯入歷史及其他玩家更新，不還原整份舊資料庫。
- 舊 `bind-manual` 只寫封存 JSON，重啟不會將它套用至正式資料庫。目前沒有新增 Discord 撰寫介面。

實際數量、操作指令與回復方法見 [譜面資料庫說明](ANNOTATION_DATABASE.md)。完整性與資料保全證據見 `reports/annotation-migration-live-20261016.json`；回退／重套只在副本驗證，正式庫沒有為了測試而來回切換。

## 已驗證的外部契約

查閱日期：2026-10-13。

- [Chunirec owner profile](https://developer.chunirec.net/docs/v2.0/methods-profile)
- [Chunirec 全曲成績](https://developer.chunirec.net/docs/v2.0/methods-records)
- [Chunirec region 定義](https://developer.chunirec.net/docs/v2.0/regions)
- [Chunirec Token](https://developer.chunirec.net/docs/v2.0/access-token)
- [Chunirec 請求額度](https://developer.chunirec.net/docs/v2.0/request-limit)
- [Chunilib 官方網站](https://chuni.tsaibee.org/) 與既有本機前端／API 盤點；公開曲庫與受保護玩家 API 是不同能力，沒有繞過驗證。

Chunirec 的 Rating RECENT 枠是 Rating 組成資料，不能作為最近遊玩歷史。本專案未實作這種推測。
