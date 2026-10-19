from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from penguin.annotation_source import load_source

DATA_SHEETS = ("13.7以下", "13.7-13.9", "14", "14+", "15以上")
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _xml_cell(column: str, row: int, value: object) -> str:
    if isinstance(value, str):
        return f'<c r="{column}{row}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    return f'<c r="{column}{row}"><v>{value}</v></c>'


def _package_bytes(
    *,
    chart_id: int = 20,
    extra_row: bool = False,
    duplicate_row: bool = False,
    extra_mapping_without_title: bool = False,
    zip_traversal: bool = False,
) -> bytes:
    row_values: dict[int, dict[str, object]] = {
        2: {
            "A": 11.9,
            "B": "Song",
            "C": "Variety",
            "D": "Official page",
            "E": "切",
            "F": "★★☆☆☆",
            "H": "A useful note",
            "I": "Local link",
            "J": "preserve this extra value",
            "L": 10,
            "M": chart_id,
            "N": "MAS",
            "O": "EXACT_TITLE_DIFFICULTY",
            "P": 21.5,
            "Q": "Song",
            "R": 0,
        }
    }
    if extra_row:
        row_values[3] = {"A": 13.8, "B": "Unmapped source", "C": "P&A", "E": "slide"}
    if extra_mapping_without_title:
        row_values[4] = {"M": 12345}

    rows: list[str] = []
    for row_number, values in row_values.items():
        cells = "".join(
            _xml_cell(column, row_number, value) for column, value in values.items()
        )
        row_xml = f'<row r="{row_number}">{cells}</row>'
        rows.append(row_xml)
        if duplicate_row and row_number == 2:
            rows.append(row_xml)

    worksheet = (
        f'<worksheet xmlns="{SHEET_NS}" xmlns:r="{REL_NS}">'
        f"<sheetData>{''.join(rows)}</sheetData>"
        '<hyperlinks><hyperlink ref="D2" r:id="rId1" display="Official page"/>'
        '<hyperlink ref="I2" r:id="rId2" display="Local link"/>'
        '<hyperlink ref="J2" r:id="rId3" display="Private address"/></hyperlinks>'
        "</worksheet>"
    )
    workbook = (
        f'<workbook xmlns="{SHEET_NS}" xmlns:r="{REL_NS}"><sheets>'
        + "".join(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, name in enumerate(DATA_SHEETS, 1)
        )
        + "</sheets></workbook>"
    )
    workbook_rels = (
        f'<Relationships xmlns="{PKG_REL_NS}">'
        + "".join(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, 6)
        )
        + "</Relationships>"
    )
    worksheet_rels = (
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/chart" TargetMode="External"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="javascript:alert(1)" TargetMode="External"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="http://127.0.0.1/private" TargetMode="External"/>'
        "</Relationships>"
    )

    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for index in range(1, 6):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                worksheet
                if index == 1
                else f'<worksheet xmlns="{SHEET_NS}"><sheetData/></worksheet>',
            )
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", worksheet_rels)
        if zip_traversal:
            archive.writestr("../outside.txt", "must be rejected")
    return buffer.getvalue()


def _fixture(
    tmp_path: Path, **package_options: object
) -> tuple[Path, Path, Path, Path]:
    base = tmp_path / "ChunithmChart_with_ID.xlsx"
    base_bytes = _package_bytes(
        extra_row=bool(package_options.get("extra_row")),
        extra_mapping_without_title=bool(
            package_options.get("extra_mapping_without_title")
        ),
    )
    base.write_bytes(base_bytes)
    luna_dir = tmp_path / "luna"
    luna_dir.mkdir()
    reviewed = luna_dir / "ChunithmChart_reviewed.xlsx"
    reviewed.write_bytes(_package_bytes(**package_options))

    manifest = tmp_path / "manifest.json"
    review = luna_dir / "review.json"
    legacy = tmp_path / "legacy.json"
    selected = {
        "chart_id": 20,
        "song_id": 10,
        "difficulty": "MAS",
        "const": 11.9,
        "exact_title": True,
    }
    _write_json(
        manifest,
        {
            "source_sha256": "a" * 64,
            "rows": [
                {
                    "source_row": 0,
                    "title": "Song",
                    "difficulty": "MAS",
                    "source_const": 11.9,
                    "source_notes": None,
                    "locations": [{"sheet": "13.7以下", "excel_row": 2}],
                    "status": "EXACT_TITLE_DIFFICULTY",
                    "selected": selected,
                    "candidates": [selected],
                }
            ],
        },
    )
    _write_json(
        review,
        {
            "source_workbook_sha256": "a" * 64,
            "base_workbook": r"D:\source\ChunithmChart_with_ID.xlsx",
            "base_workbook_sha256": hashlib.sha256(base_bytes).hexdigest(),
            "summary": {"total_reviewed": 0, "verified": 0, "unresolved": 0},
            "verification": {"source_hash_unchanged": True},
            "rows": [],
        },
    )
    _write_json(
        legacy,
        {
            "charts": [
                {
                    "title": "Song",
                    "difficulty": "Master",
                    "const": 11.9,
                    "tags": ["切"],
                    "sss_stars": 2,
                    "notes": None,
                    "genre": "Variety",
                    "note": "A useful note",
                    "flags": [],
                }
            ]
        },
    )
    return reviewed, manifest, review, legacy


def test_actual_reviewed_source_loads_and_preserves_unmapped_rows() -> None:
    root = Path(__file__).resolve().parents[1]
    result = load_source(
        root / "outputs/chart-mapping-20261015/luna/ChunithmChart_reviewed.xlsx",
        root / "outputs/chart-mapping-20261015/bm25-matches.json",
        root / "outputs/chart-mapping-20261015/luna/review.json",
        root / "snapshots/manual.json",
    )

    assert result["namespace"] == "poopenguin_manual"
    assert len(result["revision"]) == 64
    assert len(result["rows"]) == 834
    assert len({row["chart_id"] for row in result["rows"]}) == 834
    assert result["provenance"]["match_counts"] == {
        "automatic_exact": 826,
        "agent_reviewed": 8,
    }
    assert result["provenance"]["annotation_counts"] == {
        "tags": 765,
        "tagged_rows": 462,
        "notes": 600,
        "source_notes": 117,
        "links": 103,
    }
    assert result["rows"][0]["match_method"] == "automatic_exact"
    assert (
        result["rows"][0]["review_json"]["evidence_type"] == "manifest_automatic_exact"
    )

    excluded = result["provenance"]["excluded_unmapped_rows"]
    assert [(row["sheet"], row["excel_row"], row["title"]) for row in excluded] == [
        ("14", 335, "Imprinting"),
        ("15以上", 38, "PhenomenoN"),
    ]
    imprinting, phenomenon = excluded
    assert set("ABCDEFGHIJ").issubset(imprinting["raw_cells"])
    assert imprinting["raw_cells"]["H"].startswith("對手指要求較高")
    assert "NO_SOURCE_CONST" in imprinting["reason_codes"]
    assert "NOT_IN_LEGACY_SNAPSHOT" in imprinting["reason_codes"]
    assert phenomenon["raw_cells"]["A"] == "■ 15.0"
    assert phenomenon["raw_cells"]["_metadata"]["A"]["rich_text"][0]["color"] == {
        "rgb": "FF9900FF"
    }
    assert "NO_EXPLICIT_DIFFICULTY" in phenomenon["reason_codes"]
    assert "LEGACY_TITLE_HAS_DIFFERENT_CONST" in phenomenon["reason_codes"]
    assert phenomenon["links"] == []


def test_synthetic_reader_keeps_safe_links_and_raw_unsafe_targets(
    tmp_path: Path,
) -> None:
    result = load_source(*_fixture(tmp_path))
    row = result["rows"][0]

    assert row["links"] == [
        {"cell": "D2", "label": "Official page", "url": "https://example.com/chart"}
    ]
    assert row["raw_cells"]["J"] == "preserve this extra value"
    assert (
        row["raw_cells"]["_metadata"]["I"]["hyperlinks"][0]["target"]
        == "javascript:alert(1)"
    )
    assert (
        row["raw_cells"]["_metadata"]["J"]["hyperlinks"][0]["target"]
        == "http://127.0.0.1/private"
    )


def test_unmapped_source_row_is_preserved_but_extra_mapping_is_rejected(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path, extra_row=True)
    result = load_source(*paths)
    excluded = result["provenance"]["excluded_unmapped_rows"]
    assert len(excluded) == 1
    assert excluded[0]["title"] == "Unmapped source"
    assert excluded[0]["raw_cells"]["B"] == "Unmapped source"

    other = tmp_path / "mapped-extra"
    other.mkdir()
    paths = _fixture(other, extra_mapping_without_title=True)
    with pytest.raises(ValueError, match=r"^UNMAPPED_ROW_HAS_MAPPING$"):
        load_source(*paths)


def test_reviewed_workbook_id_change_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, chart_id=21)
    with pytest.raises(ValueError, match=r"^WORKBOOK_MAPPING_MISMATCH$"):
        load_source(*paths)


def test_duplicate_worksheet_row_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, duplicate_row=True)
    with pytest.raises(ValueError, match=r"^WORKSHEET_ROW_INVALID$"):
        load_source(*paths)


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, zip_traversal=True)
    with pytest.raises(ValueError, match=r"^ZIP_PATH_UNSAFE$"):
        load_source(*paths)
