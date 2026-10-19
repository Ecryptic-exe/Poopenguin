# 譜面標籤資料庫

- 這次整理了 **834 筆譜面資料**：462 筆有標籤，共 765 個標籤關聯、600 則心得及 103 個安全連結。
- 原始欄位 **A:J**、來源與來源註記會一併保留。826 筆由自動精確比對確認，另有 8 筆由 Luna agent 核對；來源 XLSX／JSON 原檔也保留作為證據。
- 標籤資料存放於獨立資料表，不覆寫原曲庫或玩家資料。Song 詳情與標籤搜尋改由資料庫讀取。
- 原始資料中兩列沒有可確認譜面編號：`Imprinting`（工作表「14」、第 335 列）及 `PhenomenoN`（工作表「15以上」、第 38 列）。兩列完整來源內容已連同 provenance 保存在 `source_snapshots`；不猜測 ID，也不啟用這兩列。
- **2026-10-16 已完成正式匯入。** 正式庫的原有 25 張資料表及既有來源紀錄比對一致，完整性與外鍵正常；相同來源重跑沒有新增重複資料。回退／重套已先在副本驗證。證據見 `reports/annotation-migration-live-20261016.json` 與排演報告。
- 這次沒有新增必要環境設定，也沒有修改 Poopenguin UI、Compare 或 command。

## 維護方式

更新人工譜面資料前，先暫停 bot，再用 `backup_database` 對目前資料庫做一致性備份。備份目的檔必須是尚不存在的新檔名；備份摘要會回報完整性、外鍵錯誤數、SHA-256 及大小，不含玩家列資料。

```powershell
# 先選一個尚不存在的備份檔名；範例目錄可由工具建立
.\.venv\Scripts\python.exe -X utf8 -c "from pathlib import Path; from penguin.annotation_backup import backup_database; print(backup_database(Path('data/penguin.sqlite3'), Path('data/backups/annotations-before-update.sqlite3')))"
```

更新時需重新產生相符的 XLSX、對照與核對檔；不能只改已封存檔案的 ID。用核對過的來源執行匯入。預設是 dry run，只檢查並顯示結果；確認內容正確後才加 `--apply` 寫入：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m penguin import-annotations
.\.venv\Scripts\python.exe -X utf8 -m penguin import-annotations --apply
```

若需回退，只指定這次匯入回報的實際 batch ID。預設先 dry run；確認後加 `--apply` 才會回退該批：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m penguin rollback-annotations --batch-id "annotation:3c4cbd49f22279525ccb5523903d7995a2036b1c15cea79d70ee30d1e826c89f"
.\.venv\Scripts\python.exe -X utf8 -m penguin rollback-annotations --batch-id "annotation:3c4cbd49f22279525ccb5523903d7995a2036b1c15cea79d70ee30d1e826c89f" --apply
```

舊的 `bind-manual` 只寫入 archive 關聯檔；重啟 bot 不會把它更新到 production。要更新 production，請透過審核過的 XLSX 和 `import-annotations` 匯入。

目前未版本化的 live 資料庫由 importer 首次加入兩張標籤資料表，沒有 Alembic stamp/head 回放。新增的 Alembic migration 會先比對既有表；只有表結構完全相同時才接受已存在的表，這項相容行為已用隔離測試驗證。

正式套用前的一致性備份：`data/backups/annotations-20261016-193852-590407/before.sqlite3`。這份本機備份含既有資料；一般回退請使用上方批次工具，無需以整份舊備份覆蓋目前資料庫。
