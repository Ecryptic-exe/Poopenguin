# 經節資料與授權

三份 JSONL 各涵蓋舊約及新約的 66 卷，依來源書卷與節號保留經節及合併節。轉檔去除 USFX 標記、標題、註腳與交叉參照，整理排版空白，加入譯本識別欄位。經文正文的字詞和標點依來源保留。

## 繁中新標點和合本

- 資料檔為 `editions/cuvt.jsonl`，31,021 筆。
- 來源為 eBible.org 的繁體新標點和合本上帝版 `cmn-cu89t`，2026 年 11 月 9 日封裝。原數位版本標示 Public Domain。[版本與授權](https://ebible.org/find/details.php?id=cmn-cu89t) · [USFX](https://ebible.org/Scriptures/cmn-cu89t_usfx.zip)
- 來源 SHA-256 為 `512410390f721e3bb0d6c096213b196609e45508c98c7d5e2710fab2607c7a01`。

## 繁中當代譯本

- 資料檔為 `editions/occb-t.jsonl`，30,961 筆。
- 來源為 2023 版的繁體開放當代譯本 `cmncbt`，使用 eBible.org 的 2025 年數位來源。原作免費提供於 [Biblica](https://www.biblica.com/) 和 [open.bible](https://open.bible/)。[版本與版權聲明](https://ebible.org/find/details.php?id=cmncbt) · [USFX](https://ebible.org/Scriptures/cmncbt_usfx.zip)
- 原作及此 JSONL 轉檔採 [Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/)。轉檔為每節一列，去除排版標記與非正文資料；本專案的呈現不代表原出版方背書。
- 來源 SHA-256 為 `11134e1165bf486f107d2dc1fbb89745d24aa2366fa535a336f6ecf3b36e6a16`。

原作署名

> Biblica® 聖經，當代譯本™開放資源<br>
> 版權所有 © 1979, 2005, 2007, 2012, 2023 Biblica, Inc.<br>
> Biblica® Open Chinese Contemporary Bible™ (Traditional Script)<br>
> Copyright © 1979, 2005, 2007, 2012, 2023 by Biblica, Inc.

Biblica® 為 Biblica, Inc. 在美國專利商標局註冊的商標。標示用於原作署名；轉檔與 Bot 使用「繁中當代譯本」作為資料識別。

## World English Bible

- 資料檔為 `editions/web.jsonl`，31,098 筆。
- 來源為 eBible.org 的 `engwebp` 66 卷英文本，使用 2026 年 11 月 21 日封裝。原作標示 Public Domain。[版本與授權](https://ebible.org/find/details.php?id=engwebp) · [USFX](https://ebible.org/Scriptures/engwebp_usfx.zip)
- 來源 SHA-256 為 `766ff6b161d09300ff954f40f62d061c9f6774d735c2c2e1573a3b28d5211679`。

把三份來源 ZIP 放在同一資料夾後，可用 `python chuni_penguin/cogs/poopenguin/scripture_cog/build_verses.py 來源資料夾 chuni_penguin/cogs/poopenguin/scripture_cog/editions` 離線重建。

BM25 的參數與正值 IDF 採用 [Apache Lucene 的 BM25Similarity](https://lucene.apache.org/core/9_5_0/core/org/apache/lucene/search/similarities/BM25Similarity.html) 所列公式。
