"""Rank community chart rows using BM25; never modify the live database."""

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"D:\WorkSpace\Codex_Workspace\chunilib_L_ver\workspace\Poopenguin-reference\Pooping Penguin\tools\ChunithmChart.xlsx")
OUT = ROOT / "outputs/chart-mapping-20261015"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
DIFF = {"Basic": "BAS", "Advanced": "ADV", "Expert": "EXP", "Master": "MAS", "Ultima": "ULT"}


def normalize(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def tokens(value):
    text = normalize(value)
    result = ["c:" + ch for ch in text]
    for width in (2, 3):
        result.extend(str(width) + ":" + text[i:i + width] for i in range(len(text) - width + 1))
    result.extend("w:" + w for w in re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", value).casefold()))
    return result


def source_locations():
    locations = defaultdict(list)
    with zipfile.ZipFile(SOURCE) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(x.itertext()) for x in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        rels = {r.attrib["Id"]: r.attrib["Target"].lstrip("/") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            name = sheet.attrib["name"]
            if name not in ["13.7以下", "13.7-13.9", "14", "14+", "15以上"]:
                continue
            path = rels[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
            if not path.startswith("xl/"):
                path = "xl/" + path
            for row in ET.fromstring(z.read(path)).findall("s:sheetData/s:row", NS):
                cells = {}
                for cell in row:
                    value = cell.find("s:v", NS)
                    text = value.text if value is not None else "".join(cell.find("s:is", NS).itertext()) if cell.find("s:is", NS) is not None else ""
                    if cell.attrib.get("t") == "s":
                        text = shared[int(text)]
                    cells[re.sub(r"\d", "", cell.attrib["r"])] = text
                title = cells.get("B", "").strip()
                const = re.search(r"\d+(?:\.\d+)?", cells.get("A", ""))
                if title and const:
                    locations[(title, float(const.group()))].append({"sheet": name, "excel_row": int(row.attrib["r"])})
    return locations


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect((ROOT / "data/penguin.sqlite3").as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    catalog = [dict(r) for r in conn.execute("SELECT c.id AS chart_id,c.song_id,c.difficulty,c.const,c.maxcombo,c.tachi_chart_id,s.title,s.artist,s.chunirec_id FROM chunirec_charts c JOIN chunirec_songs s ON s.id=c.song_id ORDER BY c.id")]
    conn.close()
    songs = {r["song_id"]: r for r in catalog}
    charts = defaultdict(list)
    for chart in catalog:
        charts[(chart["song_id"], chart["difficulty"])].append(chart)
    seeds = json.loads((ROOT / "chuni_penguin/database/seeds/songs.json").read_text(encoding="utf-8"))
    aliases = {s["id"]: s.get("aliases", []) for s in seeds}
    docs = {sid: Counter(tokens(song["title"]) + [t for alias in aliases.get(sid, []) for t in tokens(alias)]) for sid, song in songs.items()}
    freq = Counter(t for doc in docs.values() for t in doc)
    average = sum(map(lambda d: sum(d.values()), docs.values())) / len(docs)
    exact = defaultdict(list)
    for sid, song in songs.items():
        exact[normalize(song["title"])].append(sid)
    rows = json.loads((ROOT / "snapshots/manual.json").read_text(encoding="utf-8"))["charts"]
    locations = source_locations()
    result = []
    for index, row in enumerate(rows):
        difficulty = DIFF[row["difficulty"]]
        query = set(tokens(row["title"]))
        ranked = []
        for sid, doc in docs.items():
            if not charts.get((sid, difficulty)):
                continue
            length = sum(doc.values())
            score = sum(math.log(1 + (len(docs) - freq[t] + .5) / (freq[t] + .5)) * doc[t] * 2.5 / (doc[t] + 1.5 * (.25 + .75 * length / average)) for t in query if t in doc)
            if score > 0:
                for chart in charts[(sid, difficulty)]:
                    ranked.append({**chart, "bm25": round(score, 6), "exact_title": normalize(chart["title"]) == normalize(row["title"]), "notes_match": row.get("notes") is not None and row["notes"] == chart["maxcombo"]})
        ranked.sort(key=lambda c: (c["exact_title"], c["bm25"], c["notes_match"]), reverse=True)
        exact_count = sum(c["exact_title"] for c in ranked)
        status = "EXACT_TITLE_DIFFICULTY" if exact_count == 1 else "NEEDS_REVIEW"
        if exact_count == 1 and ranked[0]["notes_match"]:
            status = "EXACT_TITLE_DIFFICULTY_NOTES"
        candidates = ranked[:5]
        found = locations[(row["title"], float(row["const"]))]
        result.append({"source_row": index, "title": row["title"], "difficulty": difficulty, "source_const": row["const"], "source_notes": row.get("notes"), "locations": found, "status": status, "selected": candidates[0] if status != "NEEDS_REVIEW" else None, "candidates": candidates})
    payload = {"source_workbook": str(SOURCE), "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "database": "data/penguin.sqlite3", "method": "BM25 k1=1.5 b=.75; NFKC alphanumeric character 1/2/3-grams and Latin words, exact-title/difficulty priority; approximate ranks remain unconfirmed", "counts": dict(Counter(r["status"] for r in result)), "rows": result}
    (OUT / "bm25-matches.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "unresolved.json").write_text(json.dumps([r for r in result if r["status"] == "NEEDS_REVIEW"], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "catalog-charts.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"counts": payload["counts"], "source_rows": len(result), "rows_without_location": sum(not r["locations"] for r in result), "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
