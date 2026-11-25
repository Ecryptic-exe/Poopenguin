# ruff: noqa: RUF001 -- Perlica preserves original fullwidth punctuation.
"""Convert the supported eBible USFX archives to verse-level UTF-8 JSONL."""

import argparse
import json
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

CANON = [
    "GEN",
    "EXO",
    "LEV",
    "NUM",
    "DEU",
    "JOS",
    "JDG",
    "RUT",
    "1SA",
    "2SA",
    "1KI",
    "2KI",
    "1CH",
    "2CH",
    "EZR",
    "NEH",
    "EST",
    "JOB",
    "PSA",
    "PRO",
    "ECC",
    "SNG",
    "ISA",
    "JER",
    "LAM",
    "EZK",
    "DAN",
    "HOS",
    "JOL",
    "AMO",
    "OBA",
    "JON",
    "MIC",
    "NAM",
    "HAB",
    "ZEP",
    "HAG",
    "ZEC",
    "MAL",
    "MAT",
    "MRK",
    "LUK",
    "JHN",
    "ACT",
    "ROM",
    "1CO",
    "2CO",
    "GAL",
    "EPH",
    "PHP",
    "COL",
    "1TH",
    "2TH",
    "1TI",
    "2TI",
    "TIT",
    "PHM",
    "HEB",
    "JAS",
    "1PE",
    "2PE",
    "1JN",
    "2JN",
    "3JN",
    "JUD",
    "REV",
]
EDITIONS = {
    "cmn-cu89t": ("繁中新標點和合本", "cuvt.jsonl"),
    "cmncbt": ("繁中當代譯本", "occb-t.jsonl"),
    "engwebp": ("WEB", "web.jsonl"),
}
FORMAT_TAGS = {"pn", "add", "qs", "w", "wj", "k", "ref", "bk", "nd", "it", "em", "bd"}
NOTES = {"f", "x", "fig", "fe"}
HEADING_STYLES = {"mt", "r", "sp", "ms", "ip", "ili", "is"}
VERSE_NUMBER = re.compile(r"[1-9][0-9]*[a-z]?(?:[-–][1-9][0-9]*[a-z]?)?")


def content_events(block):
    yield block.text or ""
    for child in block:
        if child.tag in ("v", "ve"):
            yield child
        elif child.tag in FORMAT_TAGS:
            yield from content_events(child)
        elif child.tag not in NOTES:
            detail = f"Unknown inline USFX marker: {child.tag}"
            raise ValueError(detail)
        yield child.tail or ""


def book_verses(book, english):
    chapter, verse, parts = 0, None, []

    def finish():
        text = "".join(parts)
        if english:
            text = " ".join(text.split())
        else:
            text = re.sub(r"[ \t]*\n[ \t]*", "", text).replace("\u2005", " ").strip()
        if verse and text:
            if not VERSE_NUMBER.fullmatch(verse):
                detail = f"Unsupported verse label: {verse}"
                raise ValueError(detail)
            yield chapter, verse.replace("-", "–"), text

    for block in book:
        if block.tag == "c":
            yield from finish()
            chapter, verse, parts = int(block.get("id")), None, []
        if block.tag not in ("p", "q") or block.get("sfm") in HEADING_STYLES:
            continue
        for event in content_events(block):
            if isinstance(event, str):
                if verse is not None:
                    parts.append(event)
            else:
                yield from finish()
                verse, parts = (event.get("id") if event.tag == "v" else None), []
    yield from finish()


def build(archive_path, edition_id, destination):
    title, filename = EDITIONS[edition_id]
    with ZipFile(archive_path) as archive:
        data = ElementTree.fromstring(archive.read(f"{edition_id}_usfx.xml"))
        names = ElementTree.fromstring(archive.read("BookNames.xml"))
    names_by_code = {item.get("code"): item.get("short") for item in names}
    books = {book.get("id"): book for book in data.findall("book")}
    seen = set()
    with (destination / filename).open("w", encoding="utf-8", newline="\n") as target:
        for code in CANON:
            for chapter, verse, text in book_verses(
                books[code], edition_id == "engwebp"
            ):
                key = (code, chapter, verse)
                if key in seen:
                    detail = f"Duplicate verse: {key}"
                    raise ValueError(detail)
                seen.add(key)
                row = {
                    "book": names_by_code[code],
                    "chapter": chapter,
                    "verse": verse,
                    "text": text,
                    "edition": title,
                }
                if not text.isprintable() or len(text) > 1800:
                    detail = f"Unsupported text at {key}"
                    raise ValueError(detail)
                target.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
    print(f"{filename}: {len(seen)} rows across {len({ref[0] for ref in seen})} books")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    for edition in EDITIONS:
        build(args.source_directory / f"{edition}_usfx.zip", edition, args.destination)


if __name__ == "__main__":
    main()
