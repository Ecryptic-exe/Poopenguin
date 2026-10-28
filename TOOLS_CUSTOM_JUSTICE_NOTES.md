# Tools 自訂 JUSTICE

重新啟動 Bot 後，開啟 `/tools` → 容錯計算，查詢歌曲或直接輸入總物量。結果中的 **Custom JUSTICE／自訂 JUSTICE** 按鈕會開啟同一個輸入視窗，提交後更新原本的訊息。

- Tools 與歌曲資訊卡共用輸入驗證、計算函式及結果欄位，保留既有 MISS 配額優先、再取最大 ATTACK 的方式。
- 支援中英文、原使用者操作限制及 10 分鐘逾時。
- 使用「修改參數」重新查詢會清除自訂結果；舊資訊卡的表單會失效。
- 重複輸入新的 JUSTICE 數值會取代上一組自訂結果。
- 沒有新增設定、依賴套件或斜線指令，不需要重新同步指令。

例如圖中的 2,303 notes、JUSTICE = 90，結果為：99AJ N/A、SSS+ 90-2-0、SSS 90-9-0、SS+ 90-14-3、SS 90-27-8、S+ 90-52-18、S 90-77-40。

變更檔案：`cogs/chunithm/calculators.py`、`ui/custom_justice.py`、`ui/song_details.py`、新增 `ui/border.py`（均位於 `chuni_penguin/`），及 `tests/test_panels.py`。測試涵蓋 Tools 的兩種查詢、同訊息更新、重複輸入、重新查詢、擁有者、無效輸入、逾時與中英文一致性。
