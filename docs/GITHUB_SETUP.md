# 從 GitHub 安裝

公開儲存庫：[Loli45D/LB_Penguin](https://github.com/Loli45D/LB_Penguin)，預設分支 `main`。

儲存庫包含程式、必要資產、測試及已審核的社群譜面來源。`.env`、`bot.ini`、真實玩家資料庫、憑證、抽卡／管理資料、執行紀錄及快取不會上傳；現有電腦的資料保留在原處。

## 全新安裝

使用 Python 3.14 與 uv，在下載後的專案資料夾執行：

```powershell
uv sync --no-dev --extra rating
Copy-Item bot.example.ini bot.ini
Copy-Item .env.example .env
```

在自己的 `.env` 填入 Discord `TOKEN`。依 `bot.example.ini` 調整 Bot 設定，再建立新的本機資料庫：

```powershell
uv run --no-sync python -X utf8 -m penguin init
uv run --no-sync python -X utf8 -m penguin import-annotations --apply
uv run --no-sync python -X utf8 -m penguin check
uv run --no-sync python -X utf8 launcher.py --env-file .env
```

`init` 只用公開種子建立新資料庫，已有檔案時拒絕覆寫。`import-annotations` 匯入隨附且已對應的 834 筆社群譜面資料，不包含既有使用者帳號或私人評論。

上面的複製／初始化步驟只用於全新安裝；已有部署時保留自己的設定和資料。既有資料庫升級與一致性備份，請參考 [資料庫說明](ANNOTATION_DATABASE.md) 及 [評價說明](CHART_REVIEWS.md)。

## 部署提醒

- Bot 啟動不會自動更新 Discord 的指令註冊。首次部署請由擁有者依原流程同步到自己的伺服器。
- 成績分佈使用公開 Chunirec 資料與 C 產圖依賴；首次所選譜面需要網路，之後使用快取。
- Poopenguin 選配模組仍依 `.env.example`／設定檔啟用；沒有自動附帶管理資料或外部服務憑證。
- `reports/` 的測試及執行報告、其他產出圖與本機工作紀錄是執行後產生的檔案，未將本機紀錄當成部署必備資料。
- `.gitattributes` 保留來源證據與譜面資料的原始位元組，避免 Windows 換行轉換破壞輸入雜湊。

上游來源與授權保留在 `LICENSE`、`LICENSES/`、`docs/SOURCES.md` 及 `vendor/rating_bot/assets/`。
