"""Read and validate manually annotated charts from the source XLSX package.

This module intentionally uses only the Python standard library. It never writes
to the workbook and never follows hyperlinks or makes network requests.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import math
import posixpath
import re
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

_NAMESPACE = "poopenguin_manual"
_READER_CONTRACT = "annotation-source-reader/v1; standard-library-xlsx; raw-A:J; reviewed-mapping-validation"
_DATA_SHEETS = ("13.7以下", "13.7-13.9", "14", "14+", "15以上")
_RAW_COLUMNS = tuple("ABCDEFGHIJ")
_MAP_COLUMNS = tuple("LMNOPQR")
_DIFFICULTIES = frozenset({"BAS", "ADV", "EXP", "MAS", "ULT"})
_LEGACY_DIFFICULTIES = {
    "basic": "BAS",
    "advanced": "ADV",
    "expert": "EXP",
    "master": "MAS",
    "ultima": "ULT",
}
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"s": _SHEET_NS, "r": _REL_NS, "p": _PKG_REL_NS}
_MAX_WORKBOOK_BYTES = 32 * 1024 * 1024
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_ZIP_ENTRIES = 4096
_MAX_ZIP_ENTRY_BYTES = 16 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 96 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 1000
_CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
_SAFE_TAG_ALIASES = {
    "air": "Air押",
    "air押": "Air押",
    "多air": "Air押",
    "叠": "叠鍵",
    "2叠": "叠鍵",
    "四叠": "叠鍵",
    "疊": "叠鍵",
    "四疊": "叠鍵",
    "叠鍵": "叠鍵",
    "疊鍵": "叠鍵",
    "擦り": "擦",
    "掃": "掃鍵",
    "刷": "掃鍵",
    "刷鍵": "掃鍵",
    "紅掃鍵": "掃鍵",
    "紅刷鍵": "掃鍵",
    "割": "割鍵",
    "tapslide": "Tap Slide",
}


def _fail(code: str) -> None:
    raise ValueError(code)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _input_bytes(path: Path, *, label: str, limit: int) -> bytes:
    try:
        if not path.is_file():
            _fail(f"INPUT_MISSING:{label}")
        size = path.stat().st_size
        if size > limit:
            _fail(f"INPUT_TOO_LARGE:{label}")
        data = path.read_bytes()
    except OSError:
        _fail(f"INPUT_UNREADABLE:{label}")
    if len(data) > limit:
        _fail(f"INPUT_TOO_LARGE:{label}")
    return data


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("JSON_INVALID_NUMBER")


def _load_json(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except ValueError as exc:
        if str(exc).startswith("JSON_"):
            raise
        _fail("JSON_INVALID")
    except (UnicodeDecodeError, RecursionError):
        _fail("JSON_INVALID")


def _as_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _as_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_id(value: Any) -> int:
    if not _is_int(value) or value <= 0:
        _fail("MAPPING_IDS_INVALID")
    return value


def _normalized_const(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        _fail("CONST_INVALID")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        _fail("CONST_INVALID")
    if not result.is_finite() or result <= 0:
        _fail("CONST_INVALID")
    return result


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(
        part not in ("", ".", "..") for part in path.parts
    )


class _Package:
    """A bounded, traversal-checked view over an XLSX ZIP package."""

    def __init__(self, payload: bytes) -> None:
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(payload), "r")
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
            _fail("ZIP_INVALID")
        infos = self._zip.infolist()
        if len(infos) > _MAX_ZIP_ENTRIES:
            _fail("ZIP_ENTRY_LIMIT")
        names: set[str] = set()
        total = 0
        for info in infos:
            if not _safe_member_name(info.filename):
                _fail("ZIP_PATH_UNSAFE")
            if info.filename in names:
                _fail("ZIP_DUPLICATE_PART")
            names.add(info.filename)
            if info.flag_bits & 0x1:
                _fail("ZIP_ENCRYPTED")
            if info.file_size > _MAX_ZIP_ENTRY_BYTES:
                _fail("ZIP_ENTRY_LIMIT")
            total += info.file_size
            if total > _MAX_ZIP_TOTAL_BYTES:
                _fail("ZIP_TOTAL_LIMIT")
            if info.file_size and not info.compress_size:
                _fail("ZIP_EXPANSION_LIMIT")
            if (
                info.compress_size
                and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO
            ):
                _fail("ZIP_EXPANSION_LIMIT")
        self._names = names

    def read(self, part: str, *, limit: int = _MAX_ZIP_ENTRY_BYTES) -> bytes:
        if not _safe_member_name(part) or part not in self._names:
            _fail("XLSX_PART_MISSING")
        try:
            value = self._zip.read(part)
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
            _fail("ZIP_INVALID")
        if len(value) > limit:
            _fail("ZIP_ENTRY_LIMIT")
        return value

    def close(self) -> None:
        self._zip.close()


def _xml(package: _Package, part: str) -> ET.Element:
    payload = package.read(part)
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail("XML_UNSAFE")
    try:
        return ET.fromstring(payload)
    except (ET.ParseError, ValueError, RecursionError):
        _fail("XML_INVALID")


def _resolve_part(source_part: str, target: str) -> str:
    if not target or "\\" in target or "\x00" in target:
        _fail("XLSX_RELATIONSHIP_INVALID")
    if urlsplit(target).scheme or urlsplit(target).netloc:
        _fail("XLSX_RELATIONSHIP_INVALID")
    segments = (
        []
        if target.startswith("/")
        else list(PurePosixPath(posixpath.dirname(source_part)).parts)
    )
    for part in PurePosixPath(target.lstrip("/")).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not segments:
                _fail("ZIP_PATH_UNSAFE")
            segments.pop()
        else:
            segments.append(part)
    path = "/".join(segments)
    if not _safe_member_name(path):
        _fail("ZIP_PATH_UNSAFE")
    return path


def _relationships(
    package: _Package, rel_part: str, source_part: str
) -> dict[str, dict[str, str]]:
    if rel_part not in package._names:
        return {}
    root = _xml(package, rel_part)
    result: dict[str, dict[str, str]] = {}
    for rel in root.findall("p:Relationship", _NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if not rel_id or not target or rel_id in result:
            _fail("XLSX_RELATIONSHIP_INVALID")
        result[rel_id] = {
            "target": target,
            "target_mode": rel.attrib.get("TargetMode", ""),
            "type": rel.attrib.get("Type", ""),
        }
        if result[rel_id]["target_mode"] != "External":
            result[rel_id]["part"] = _resolve_part(source_part, target)
    return result


def _cell_ref(cell: ET.Element, row_number: int) -> tuple[str, str]:
    ref = cell.attrib.get("r", "")
    match = _CELL_REF.fullmatch(ref)
    if match is None or int(match.group(2)) != row_number:
        _fail("WORKSHEET_CELL_REF_INVALID")
    return ref, match.group(1)


def _rich_runs(container: ET.Element | None) -> list[dict[str, Any]]:
    if container is None:
        return []
    result: list[dict[str, Any]] = []
    for child in list(container):
        if child.tag == f"{{{_SHEET_NS}}}t":
            result.append({"text": child.text or ""})
        elif child.tag == f"{{{_SHEET_NS}}}r":
            text = "".join(node.text or "" for node in child.findall("s:t", _NS))
            run: dict[str, Any] = {"text": text}
            props = child.find("s:rPr", _NS)
            if props is not None:
                color = props.find("s:color", _NS)
                if color is not None:
                    run["color"] = dict(color.attrib)
                properties: dict[str, Any] = {}
                for prop in list(props):
                    name = prop.tag.rsplit("}", 1)[-1]
                    properties[name] = (
                        dict(prop.attrib) if prop.attrib else (prop.text or True)
                    )
                if properties:
                    run["properties"] = properties
            result.append(run)
    return result


def _numeric_value(raw: str) -> int | float | str:
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if not number.is_finite():
        _fail("CELL_NUMBER_INVALID")
    if number == number.to_integral_value():
        return int(number)
    value = float(number)
    if not math.isfinite(value):
        _fail("CELL_NUMBER_INVALID")
    return value


def _decode_cell(
    cell: ET.Element | None, shared_strings: Sequence[ET.Element]
) -> tuple[Any, dict[str, Any]]:
    if cell is None:
        return None, {}
    cell_type = cell.attrib.get("t", "n")
    value_element = cell.find("s:v", _NS)
    inline = cell.find("s:is", _NS)
    formula = cell.find("s:f", _NS)
    rich_text: list[dict[str, Any]] = []
    if cell_type == "s":
        if value_element is None:
            _fail("SHARED_STRING_INVALID")
        try:
            index = int(value_element.text or "")
            shared = shared_strings[index]
        except (ValueError, IndexError):
            _fail("SHARED_STRING_INVALID")
        rich_text = _rich_runs(shared)
        value: Any = (
            "".join(run["text"] for run in rich_text)
            if rich_text
            else "".join(node.text or "" for node in shared.iter(f"{{{_SHEET_NS}}}t"))
        )
    elif cell_type == "inlineStr":
        rich_text = _rich_runs(inline)
        value = "".join(run["text"] for run in rich_text)
    elif cell_type == "b":
        if value_element is None or value_element.text not in ("0", "1"):
            _fail("CELL_VALUE_INVALID")
        value = value_element.text == "1"
    elif cell_type in {"str", "e", "d"}:
        value = value_element.text if value_element is not None else None
    elif cell_type in {"n", ""}:
        value = (
            _numeric_value(value_element.text)
            if value_element is not None and value_element.text is not None
            else None
        )
    else:
        _fail("CELL_TYPE_UNSUPPORTED")
    meta: dict[str, Any] = {"type": cell.attrib.get("t", "n")}
    if "s" in cell.attrib:
        meta["style_id"] = int(cell.attrib["s"])
    if formula is not None:
        meta["formula"] = formula.text or ""
    if rich_text:
        meta["rich_text"] = rich_text
    return value, meta


def _sheet_part_map(package: _Package) -> dict[str, str]:
    workbook = _xml(package, "xl/workbook.xml")
    rels = _relationships(package, "xl/_rels/workbook.xml.rels", "xl/workbook.xml")
    sheets_element = workbook.find("s:sheets", _NS)
    if sheets_element is None:
        _fail("WORKBOOK_STRUCTURE")
    result: dict[str, str] = {}
    for sheet in sheets_element.findall("s:sheet", _NS):
        name = sheet.attrib.get("name")
        rel_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
        rel = rels.get(rel_id or "")
        if (
            not name
            or name in result
            or rel is None
            or rel.get("target_mode") == "External"
        ):
            _fail("WORKBOOK_STRUCTURE")
        if not rel.get("type", "").endswith("/worksheet"):
            _fail("WORKSHEET_RELATIONSHIP")
        result[name] = rel["part"]
    if any(name not in result for name in _DATA_SHEETS):
        _fail("WORKSHEET_MISSING")
    return result


def _parse_sheet(
    package: _Package, part: str, shared_strings: Sequence[ET.Element]
) -> dict[str, Any]:
    root = _xml(package, part)
    sheet_data = root.find("s:sheetData", _NS)
    if sheet_data is None:
        _fail("WORKSHEET_STRUCTURE")
    rows: dict[int, dict[str, ET.Element]] = {}
    for row in sheet_data.findall("s:row", _NS):
        try:
            row_number = int(row.attrib.get("r", ""))
        except ValueError:
            _fail("WORKSHEET_ROW_INVALID")
        if row_number < 1 or row_number > 1_048_576 or row_number in rows:
            _fail("WORKSHEET_ROW_INVALID")
        cells: dict[str, ET.Element] = {}
        for cell in row.findall("s:c", _NS):
            ref, _ = _cell_ref(cell, row_number)
            if ref in cells:
                _fail("WORKSHEET_CELL_DUPLICATE")
            cells[ref] = cell
        rows[row_number] = cells

    rel_part = posixpath.join(
        posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
    )
    rels = _relationships(package, rel_part, part)
    hyperlinks: dict[str, list[dict[str, Any]]] = {}
    for link in root.findall("s:hyperlinks/s:hyperlink", _NS):
        ref = link.attrib.get("ref")
        if not ref:
            _fail("HYPERLINK_INVALID")
        rel_id = link.attrib.get(f"{{{_REL_NS}}}id")
        rel = rels.get(rel_id or "") if rel_id else None
        if rel_id and rel is None:
            _fail("HYPERLINK_INVALID")
        target = rel.get("target") if rel else None
        raw = {
            "ref": ref,
            "target": target,
            "target_mode": rel.get("target_mode") if rel else None,
            "location": link.attrib.get("location"),
            "display": link.attrib.get("display"),
        }
        hyperlinks.setdefault(ref, []).append(raw)

    values: dict[str, Any] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for row_cells in rows.values():
        for ref, cell in row_cells.items():
            value, meta = _decode_cell(cell, shared_strings)
            values[ref] = value
            metadata[ref] = meta
    return {
        "rows": rows,
        "values": values,
        "metadata": metadata,
        "hyperlinks": hyperlinks,
    }


def _parse_package(payload: bytes) -> dict[str, dict[str, Any]]:
    package = _Package(payload)
    try:
        parts = _sheet_part_map(package)
        shared_strings: list[ET.Element] = []
        rels = _relationships(package, "xl/_rels/workbook.xml.rels", "xl/workbook.xml")
        for rel in rels.values():
            if (
                rel.get("type", "").endswith("/sharedStrings")
                and rel.get("target_mode") != "External"
            ):
                shared_root = _xml(package, rel["part"])
                shared_strings = shared_root.findall("s:si", _NS)
                break
        return {
            name: _parse_sheet(package, parts[name], shared_strings)
            for name in _DATA_SHEETS
        }
    finally:
        package.close()


def _safe_public_url(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(char) < 0x20 or char.isspace() for char in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False
        # Accessing .port validates that a supplied port is numeric and in range.
        _ = parsed.port
        if parsed.username is not None or parsed.password is not None:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".test")
        ):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "." in host and not host.startswith(".") and not host.endswith(".")
        else:
            return address.is_global
    except ValueError:
        return False


def _as_decimal_cell(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        _fail("SOURCE_CONST_INVALID")
    text = str(value).strip()
    match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)\s*$", text)
    if match is None:
        _fail("SOURCE_CONST_INVALID")
    try:
        number = Decimal(match.group(1))
    except InvalidOperation:
        _fail("SOURCE_CONST_INVALID")
    if not number.is_finite() or number <= 0:
        _fail("SOURCE_CONST_INVALID")
    return number


def _as_notes(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        _fail("SOURCE_NOTES_INVALID")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        _fail("SOURCE_NOTES_INVALID")
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        _fail("SOURCE_NOTES_INVALID")
    return int(number)


def _canonical_tag(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    key = re.sub(r"\s+", "", normalized).casefold()
    return _SAFE_TAG_ALIASES.get(key, normalized)


def _legacy_tags_match(source_value: Any, legacy_tags: Any) -> bool:
    if not isinstance(legacy_tags, list) or any(
        not isinstance(tag, str) for tag in legacy_tags
    ):
        return False
    raw = source_value if isinstance(source_value, str) else ""
    source_tags = [_canonical_tag(part) for part in raw.split("/") if part.strip()]
    normalized_source = [
        unicodedata.normalize("NFKC", tag).casefold() for tag in source_tags
    ]
    normalized_legacy = [
        unicodedata.normalize("NFKC", _canonical_tag(tag)).casefold()
        for tag in legacy_tags
    ]
    return normalized_source == normalized_legacy


def _source_coordinates(
    manifest_rows: list[Any],
) -> tuple[dict[int, dict[str, Any]], set[tuple[str, int]]]:
    by_source: dict[int, dict[str, Any]] = {}
    by_location: set[tuple[str, int]] = set()
    for raw in manifest_rows:
        row = _as_mapping(raw, "MANIFEST_ROW_INVALID")
        source_row = row.get("source_row")
        if not _is_int(source_row) or source_row < 0 or source_row in by_source:
            _fail("MANIFEST_SOURCE_ROW_INVALID")
        locations = _as_list(row.get("locations"), "MANIFEST_LOCATION_INVALID")
        if len(locations) != 1:
            _fail("MANIFEST_LOCATION_INVALID")
        location = _as_mapping(locations[0], "MANIFEST_LOCATION_INVALID")
        sheet, excel_row = location.get("sheet"), location.get("excel_row")
        if (
            sheet not in _DATA_SHEETS
            or not _is_int(excel_row)
            or excel_row < 2
            or excel_row > 1_048_576
        ):
            _fail("MANIFEST_LOCATION_INVALID")
        coord = (sheet, excel_row)
        if coord in by_location:
            _fail("MANIFEST_DUPLICATE_LOCATION")
        by_source[source_row] = dict(row)
        by_location.add(coord)
    if set(by_source) != set(range(len(by_source))):
        _fail("MANIFEST_SOURCE_ROW_GAP")
    return by_source, by_location


def _cell_raw(sheet: Mapping[str, Any], coordinate: str) -> tuple[Any, dict[str, Any]]:
    return sheet["values"].get(coordinate), dict(sheet["metadata"].get(coordinate, {}))


def _raw_cells(
    sheet_name: str, sheet: Mapping[str, Any], excel_row: int
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw: dict[str, Any] = {}
    metadata: dict[str, dict[str, Any]] = {}
    links: list[dict[str, str]] = []
    for column in _RAW_COLUMNS:
        coordinate = f"{column}{excel_row}"
        value, cell_meta = _cell_raw(sheet, coordinate)
        raw[column] = value
        cell_links = sheet["hyperlinks"].get(coordinate, [])
        if cell_links:
            cell_meta["hyperlinks"] = cell_links
            for link in cell_links:
                url = link.get("target")
                if not _safe_public_url(url):
                    continue
                label = link.get("display") or (value if isinstance(value, str) else "")
                links.append({"cell": coordinate, "label": str(label), "url": url})
        metadata[column] = cell_meta
    raw["_metadata"] = metadata
    return raw, links


def _row_values(sheet: Mapping[str, Any], excel_row: int) -> dict[str, Any]:
    return {
        column: sheet["values"].get(f"{column}{excel_row}")
        for column in "ABCDEFGHIJLMNOPQR"
    }


def _validate_physical_rows(
    sheets: Mapping[str, Mapping[str, Any]],
    source_locations: set[tuple[str, int]],
) -> list[tuple[str, int]]:
    actual: set[tuple[str, int]] = set()
    for sheet_name in _DATA_SHEETS:
        sheet = sheets[sheet_name]
        for row_number, cells in sheet["rows"].items():
            values = sheet["values"]
            title = values.get(f"B{row_number}")
            has_source_content = any(
                values.get(f"{column}{row_number}") not in (None, "")
                for column in _RAW_COLUMNS
            )
            has_mapping_content = any(
                values.get(f"{column}{row_number}") not in (None, "")
                for column in _MAP_COLUMNS
            ) or any(
                cells.get(f"{column}{row_number}") is not None
                and cells[f"{column}{row_number}"].find("s:f", _NS) is not None
                for column in _MAP_COLUMNS
            )
            if row_number == 1:
                continue
            if isinstance(title, str) and title.strip():
                actual.add((sheet_name, row_number))
            elif has_mapping_content:
                _fail("UNMAPPED_ROW_HAS_MAPPING")
            elif has_source_content:
                _fail("SOURCE_ROW_TITLE_MISSING")
    if not source_locations.issubset(actual):
        _fail("SOURCE_ROW_SET_MISMATCH")
    excluded = sorted(
        actual - source_locations,
        key=lambda item: (_DATA_SHEETS.index(item[0]), item[1]),
    )
    for sheet_name, row_number in excluded:
        values = sheets[sheet_name]["values"]
        if any(
            values.get(f"{column}{row_number}") not in (None, "")
            for column in _MAP_COLUMNS
        ):
            _fail("UNMAPPED_ROW_HAS_MAPPING")
        cells = sheets[sheet_name]["rows"][row_number]
        if any(
            cells.get(f"{column}{row_number}") is not None
            and cells[f"{column}{row_number}"].find("s:f", _NS) is not None
            for column in _MAP_COLUMNS
        ):
            _fail("UNMAPPED_ROW_HAS_MAPPING")
    return excluded


def _review_base_path(workbook_path: Path, review: Mapping[str, Any]) -> Path:
    declared = review.get("base_workbook")
    if not isinstance(declared, str) or not declared:
        _fail("REVIEW_BASE_PATH_INVALID")
    basename = Path(declared).name
    if basename in {"", ".", ".."} or "/" in basename or "\\" in basename:
        _fail("REVIEW_BASE_PATH_INVALID")
    # The review workbook lives in a child folder (luna); the base is read only
    # from its immediate parent, regardless of any absolute path in review.json.
    candidate = workbook_path.parent.parent / basename
    try:
        candidate.resolve().relative_to(workbook_path.parent.parent.resolve())
    except (OSError, ValueError):
        _fail("REVIEW_BASE_PATH_INVALID")
    return candidate


def _validate_hash(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        _fail("SOURCE_HASH_INVALID")
    return value.lower()


def _check_base_mapping(
    base_sheets: Mapping[str, Mapping[str, Any]],
    reviewed_sheets: Mapping[str, Mapping[str, Any]],
    manifest_by_source: Mapping[int, Mapping[str, Any]],
    review_by_source: Mapping[int, Mapping[str, Any]],
    location_by_source: Mapping[int, tuple[str, int]],
    excluded_locations: Sequence[tuple[str, int]],
) -> None:
    for sheet_name in _DATA_SHEETS:
        base = base_sheets[sheet_name]
        reviewed = reviewed_sheets[sheet_name]
        for source_row, (row_sheet, excel_row) in location_by_source.items():
            if row_sheet != sheet_name:
                continue
            # The reviewed version must retain the source A:J data and its
            # original rich text and hyperlink destinations.
            for column in _RAW_COLUMNS:
                ref = f"{column}{excel_row}"
                if _cell_raw(base, ref) != _cell_raw(reviewed, ref):
                    _fail("REVIEW_SOURCE_CELLS_CHANGED")
                base_links = base["hyperlinks"].get(ref, [])
                reviewed_links = reviewed["hyperlinks"].get(ref, [])
                if base_links != reviewed_links:
                    _fail("REVIEW_SOURCE_LINKS_CHANGED")

            manifest_row = manifest_by_source[source_row]
            values = _row_values(base, excel_row)
            auto_match = manifest_row.get("selected")
            if auto_match is None:
                if values.get("L") not in (None, "") or values.get("M") not in (
                    None,
                    "",
                ):
                    _fail("BASE_UNRESOLVED_IDS_PRESENT")
                if values.get("O") != "NEEDS_REVIEW":
                    _fail("BASE_STATUS_MISMATCH")
            else:
                selected = _as_mapping(auto_match, "MANIFEST_MATCH_INVALID")
                if values.get("L") != selected.get("song_id") or values.get(
                    "M"
                ) != selected.get("chart_id"):
                    _fail("BASE_MAPPING_MISMATCH")
                if values.get("O") != manifest_row.get("status"):
                    _fail("BASE_STATUS_MISMATCH")

            for column in "NPQR":
                if values.get(column) != reviewed["values"].get(f"{column}{excel_row}"):
                    _fail("REVIEW_NON_TARGET_CELLS_CHANGED")

            if values.get("N") != reviewed["values"].get(f"N{excel_row}"):
                _fail("REVIEW_DIFFICULTY_CHANGED")
            if (
                values.get("R") != source_row
                or reviewed["values"].get(f"R{excel_row}") != source_row
            ):
                _fail("SOURCE_ROW_MISMATCH")

    for sheet_name, excel_row in excluded_locations:
        base = base_sheets[sheet_name]
        reviewed = reviewed_sheets[sheet_name]
        for column in _RAW_COLUMNS:
            ref = f"{column}{excel_row}"
            if _cell_raw(base, ref) != _cell_raw(reviewed, ref):
                _fail("REVIEW_SOURCE_CELLS_CHANGED")
            if base["hyperlinks"].get(ref, []) != reviewed["hyperlinks"].get(ref, []):
                _fail("REVIEW_SOURCE_LINKS_CHANGED")
        for column in _MAP_COLUMNS:
            if base["values"].get(f"{column}{excel_row}") not in (None, ""):
                _fail("UNMAPPED_ROW_HAS_MAPPING")
            if reviewed["values"].get(f"{column}{excel_row}") not in (None, ""):
                _fail("UNMAPPED_ROW_HAS_MAPPING")
            if (
                base["rows"].get(excel_row, {}).get(f"{column}{excel_row}") is not None
                and base["rows"][excel_row][f"{column}{excel_row}"].find("s:f", _NS)
                is not None
            ) or (
                reviewed["rows"].get(excel_row, {}).get(f"{column}{excel_row}")
                is not None
                and reviewed["rows"][excel_row][f"{column}{excel_row}"].find("s:f", _NS)
                is not None
            ):
                _fail("UNMAPPED_ROW_HAS_MAPPING")


def load_source(
    workbook: Path, manifest: Path, review: Path, legacy: Path
) -> dict[str, Any]:
    """Load source rows from a reviewed XLSX after validating all provenance.

    ``workbook`` is the reviewed XLSX, while its pre-review base workbook is
    verified from the sibling path named in ``review``. The latter is always
    constrained to the reviewed workbook's immediate parent directory.
    """

    workbook_path, manifest_path, review_path, legacy_path = map(
        Path, (workbook, manifest, review, legacy)
    )
    workbook_bytes = _input_bytes(
        workbook_path, label="workbook", limit=_MAX_WORKBOOK_BYTES
    )
    manifest_bytes = _input_bytes(
        manifest_path, label="manifest", limit=_MAX_JSON_BYTES
    )
    review_bytes = _input_bytes(review_path, label="review", limit=_MAX_JSON_BYTES)
    legacy_bytes = _input_bytes(legacy_path, label="legacy", limit=_MAX_JSON_BYTES)

    manifest_data = _as_mapping(_load_json(manifest_bytes), "MANIFEST_INVALID")
    review_data = _as_mapping(_load_json(review_bytes), "REVIEW_INVALID")
    legacy_data = _as_mapping(_load_json(legacy_bytes), "LEGACY_INVALID")
    manifest_rows = _as_list(manifest_data.get("rows"), "MANIFEST_ROWS_INVALID")
    legacy_rows = _as_list(legacy_data.get("charts"), "LEGACY_ROWS_INVALID")
    review_rows = _as_list(review_data.get("rows"), "REVIEW_ROWS_INVALID")

    workbook_sha = _sha256(workbook_bytes)
    manifest_sha = _sha256(manifest_bytes)
    review_sha = _sha256(review_bytes)
    legacy_sha = _sha256(legacy_bytes)

    manifest_source_sha = _validate_hash(manifest_data.get("source_sha256"))
    review_source_sha = _validate_hash(review_data.get("source_workbook_sha256"))
    if manifest_source_sha != review_source_sha:
        _fail("REVIEW_SOURCE_HASH_MISMATCH")
    verification = _as_mapping(
        review_data.get("verification"), "REVIEW_VERIFICATION_INVALID"
    )
    if verification.get("source_hash_unchanged") is not True:
        _fail("REVIEW_SOURCE_HASH_UNVERIFIED")

    manifest_by_source, source_locations = _source_coordinates(manifest_rows)
    if len(legacy_rows) != len(manifest_by_source):
        _fail("LEGACY_ROW_COUNT_MISMATCH")
    if not manifest_by_source:
        _fail("SOURCE_EMPTY")

    review_by_source: dict[int, dict[str, Any]] = {}
    for value in review_rows:
        row = _as_mapping(value, "REVIEW_ROW_INVALID")
        source_row = row.get("source_row_zero_based")
        if not _is_int(source_row) or source_row < 0 or source_row in review_by_source:
            _fail("REVIEW_SOURCE_ROW_INVALID")
        review_by_source[source_row] = dict(row)
    if not set(review_by_source).issubset(manifest_by_source):
        _fail("REVIEW_SOURCE_ROW_UNKNOWN")

    workbook_sheets = _parse_package(workbook_bytes)
    excluded_locations = _validate_physical_rows(workbook_sheets, source_locations)

    base_path = _review_base_path(workbook_path, review_data)
    base_bytes = _input_bytes(base_path, label="review_base", limit=_MAX_WORKBOOK_BYTES)
    base_sha = _sha256(base_bytes)
    expected_base_sha = _validate_hash(review_data.get("base_workbook_sha256"))
    if base_sha != expected_base_sha:
        _fail("REVIEW_BASE_HASH_MISMATCH")
    base_sheets = _parse_package(base_bytes)
    base_excluded_locations = _validate_physical_rows(base_sheets, source_locations)
    if base_excluded_locations != excluded_locations:
        _fail("REVIEW_SOURCE_ROW_SET_CHANGED")

    location_by_source = {
        source_row: (location["sheet"], location["excel_row"])
        for source_row, row in manifest_by_source.items()
        for location in row["locations"]
    }
    _check_base_mapping(
        base_sheets,
        workbook_sheets,
        manifest_by_source,
        review_by_source,
        location_by_source,
        excluded_locations,
    )

    summary = _as_mapping(review_data.get("summary"), "REVIEW_SUMMARY_INVALID")
    verified_count = summary.get("verified")
    total_reviewed = summary.get("total_reviewed")
    if total_reviewed != len(review_by_source) or verified_count != len(
        review_by_source
    ):
        _fail("REVIEW_SUMMARY_MISMATCH")
    if summary.get("unresolved") != 0:
        _fail("REVIEW_UNRESOLVED")

    output_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    seen_chart_ids: set[int] = set()
    method_counts = {"automatic_exact": 0, "agent_reviewed": 0}
    tag_count = tagged_row_count = note_count = source_note_count = link_count = 0
    for source_row in range(len(manifest_by_source)):
        manifest_row = manifest_by_source[source_row]
        sheet_name, excel_row = location_by_source[source_row]
        sheet = workbook_sheets[sheet_name]
        cells = _row_values(sheet, excel_row)
        raw_cells, links = _raw_cells(sheet_name, sheet, excel_row)
        legacy_row = _as_mapping(legacy_rows[source_row], "LEGACY_ROW_INVALID")

        title = cells.get("B")
        difficulty = cells.get("N")
        if not isinstance(title, str) or not title.strip():
            _fail("SOURCE_ROW_TITLE_MISSING")
        if not isinstance(difficulty, str) or difficulty not in _DIFFICULTIES:
            _fail("SOURCE_DIFFICULTY_INVALID")
        if (
            _normalized_text(manifest_row.get("title")) != _normalized_text(title)
            or manifest_row.get("difficulty") != difficulty
        ):
            _fail("MANIFEST_SOURCE_MISMATCH")

        source_const_decimal = _as_decimal_cell(cells.get("A"))
        manifest_const = _normalized_const(manifest_row.get("source_const"))
        legacy_const = _normalized_const(legacy_row.get("const"))
        if (
            source_const_decimal != manifest_const
            or source_const_decimal != legacy_const
        ):
            _fail("SOURCE_CONST_MISMATCH")
        source_notes = _as_notes(cells.get("G"))
        if source_notes != _as_notes(
            manifest_row.get("source_notes")
        ) or source_notes != _as_notes(legacy_row.get("notes")):
            _fail("SOURCE_NOTES_MISMATCH")

        legacy_difficulty = legacy_row.get("difficulty")
        if (
            not isinstance(legacy_difficulty, str)
            or _LEGACY_DIFFICULTIES.get(legacy_difficulty.casefold()) != difficulty
        ):
            _fail("LEGACY_DIFFICULTY_MISMATCH")
        if _normalized_text(legacy_row.get("title")) != _normalized_text(title):
            _fail("LEGACY_TITLE_MISMATCH")
        if _normalized_text(legacy_row.get("genre")) != _normalized_text(
            cells.get("C")
        ):
            _fail("LEGACY_GENRE_MISMATCH")
        if _normalized_text(legacy_row.get("note")) != _normalized_text(cells.get("H")):
            _fail("LEGACY_NOTE_MISMATCH")
        if not _legacy_tags_match(cells.get("E"), legacy_row.get("tags")):
            _fail("LEGACY_TAGS_MISMATCH")
        tags = legacy_row.get("tags")
        flags = legacy_row.get("flags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            _fail("LEGACY_TAGS_INVALID")
        if not isinstance(flags, list) or any(
            not isinstance(flag, str) for flag in flags
        ):
            _fail("LEGACY_FLAGS_INVALID")

        star_text = cells.get("F")
        source_stars = (
            len(re.findall("★", star_text)) if isinstance(star_text, str) else 0
        )
        legacy_stars = legacy_row.get("sss_stars")
        if (source_stars or None) != legacy_stars:
            _fail("LEGACY_STARS_MISMATCH")
        if legacy_stars is not None and (
            not _is_int(legacy_stars) or not 1 <= legacy_stars <= 5
        ):
            _fail("LEGACY_STARS_INVALID")

        if cells.get("R") != source_row:
            _fail("SOURCE_ROW_MISMATCH")

        luna_row = review_by_source.get(source_row)
        manifest_selected = manifest_row.get("selected")
        if luna_row is None:
            if manifest_row.get("status") not in {
                "EXACT_TITLE_DIFFICULTY",
                "EXACT_TITLE_DIFFICULTY_NOTES",
            }:
                _fail("REVIEW_ROW_MISSING")
            selected = _as_mapping(manifest_selected, "MANIFEST_MATCH_INVALID")
            if (
                selected.get("exact_title") is not True
                or selected.get("difficulty") != difficulty
            ):
                _fail("MANIFEST_MATCH_NOT_EXACT")
            if not any(
                isinstance(candidate, dict)
                and candidate.get("chart_id") == selected.get("chart_id")
                and candidate.get("song_id") == selected.get("song_id")
                and candidate.get("difficulty") == difficulty
                for candidate in _as_list(
                    manifest_row.get("candidates"), "MANIFEST_CANDIDATES_INVALID"
                )
            ):
                _fail("MANIFEST_SELECTED_NOT_CANDIDATE")
            match = selected
            method = "automatic_exact"
            if cells.get("O") != manifest_row.get("status"):
                _fail("WORKBOOK_STATUS_MISMATCH")
            review_evidence = {
                "evidence_type": "manifest_automatic_exact",
                "manifest_status": manifest_row.get("status"),
                "selected": dict(selected),
            }
        else:
            if (
                manifest_selected is not None
                or manifest_row.get("status") != "NEEDS_REVIEW"
            ):
                _fail("REVIEW_MANIFEST_MISMATCH")
            if (
                luna_row.get("review_status") != "VERIFIED"
                or luna_row.get("spreadsheet_status") != "MANUALLY_VERIFIED"
            ):
                _fail("REVIEW_ROW_UNVERIFIED")
            if (
                luna_row.get("source_row_zero_based") != source_row
                or _normalized_text(luna_row.get("source_title"))
                != _normalized_text(title)
                or luna_row.get("source_difficulty") != difficulty
                or luna_row.get("source_sheet") != sheet_name
                or luna_row.get("source_excel_row") != excel_row
                or _normalized_const(luna_row.get("source_const"))
                != source_const_decimal
                or _as_notes(luna_row.get("source_notes")) != source_notes
                or _normalized_text(luna_row.get("source_genre"))
                != _normalized_text(cells.get("C"))
            ):
                _fail("REVIEW_SOURCE_ROW_MISMATCH")
            rationale = luna_row.get("identity_rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                _fail("REVIEW_EVIDENCE_MISSING")
            match = _as_mapping(luna_row.get("database_match"), "REVIEW_MATCH_INVALID")
            method = "agent_reviewed"
            if cells.get("O") != "MANUALLY_VERIFIED":
                _fail("WORKBOOK_STATUS_MISMATCH")
            review_evidence = {
                "evidence_type": "luna_row_review",
                "review": dict(luna_row),
            }

        chart_id = _positive_id(match.get("chart_id"))
        song_id = _positive_id(match.get("song_id"))
        match_difficulty = match.get("difficulty")
        if match_difficulty != difficulty:
            _fail("MAPPING_DIFFICULTY_MISMATCH")
        if chart_id in seen_chart_ids:
            _fail("MAPPING_DUPLICATE_CHART_ID")
        seen_chart_ids.add(chart_id)
        if cells.get("L") != song_id or cells.get("M") != chart_id:
            _fail("WORKBOOK_MAPPING_MISMATCH")
        stars = legacy_stars
        note = legacy_row.get("note")
        genre = legacy_row.get("genre")
        if tags:
            tag_count += len(tags)
            tagged_row_count += 1
        if note:
            note_count += 1
        if source_notes is not None:
            source_note_count += 1
        link_count += len(links)
        method_counts[method] += 1

        # Legacy normalized annotation values are imported only after their
        # source cells have been cross-checked. raw_cells retains the exact
        # workbook spellings, rich text, and every original hyperlink target.
        output_rows.append(
            {
                "source_row": source_row,
                "sheet": sheet_name,
                "excel_row": excel_row,
                "chart_id": chart_id,
                "song_id": song_id,
                "difficulty": difficulty,
                "title": title,
                "source_const": float(source_const_decimal),
                "source_notes": source_notes,
                "sss_stars": stars,
                "note": note,
                "genre": genre,
                "tags": list(tags),
                "flags": list(flags),
                "links": links,
                "raw_cells": raw_cells,
                "match_method": method,
                "review_json": review_evidence,
            }
        )

    if set(review_by_source) != {
        row["source_row"]
        for row in output_rows
        if row["match_method"] == "agent_reviewed"
    }:
        _fail("REVIEW_ROW_SET_MISMATCH")

    manifest_titles = [
        _normalized_text(row.get("title")) for row in manifest_by_source.values()
    ]
    legacy_titles: dict[str, list[Mapping[str, Any]]] = {}
    for row in legacy_rows:
        legacy_row = _as_mapping(row, "LEGACY_ROW_INVALID")
        legacy_titles.setdefault(_normalized_text(legacy_row.get("title")), []).append(
            legacy_row
        )

    for sheet_name, excel_row in excluded_locations:
        raw_cells, links = _raw_cells(
            sheet_name, workbook_sheets[sheet_name], excel_row
        )
        values = _row_values(workbook_sheets[sheet_name], excel_row)
        excluded_title = values.get("B")
        title_key = _normalized_text(excluded_title)
        matching_legacy = legacy_titles.get(title_key, [])
        rating_value = values.get("A")
        difficulty_value = values.get("N")
        excluded_const: Decimal | None = None
        reason_codes = ["NOT_IN_MANIFEST"]
        if not isinstance(difficulty_value, str) or not difficulty_value.strip():
            reason_codes.append("NO_EXPLICIT_DIFFICULTY")
        if rating_value in (None, ""):
            reason_codes.append("NO_SOURCE_CONST")
        else:
            reason_codes.append("RATING_CELL_IS_NOT_DIFFICULTY")
            try:
                excluded_const = _as_decimal_cell(rating_value)
            except ValueError:
                excluded_const = None
        if not matching_legacy:
            reason_codes.append("NOT_IN_LEGACY_SNAPSHOT")
        else:
            legacy_const_matches = 0
            if excluded_const is not None:
                legacy_const_matches = sum(
                    _normalized_const(candidate.get("const")) == excluded_const
                    for candidate in matching_legacy
                )
            if not legacy_const_matches:
                reason_codes.append("LEGACY_TITLE_HAS_DIFFERENT_CONST")
        if title_key in manifest_titles:
            reason_codes.append("MANIFEST_HAS_TITLE_AT_DIFFERENT_LOCATION_OR_VARIANT")
        reason = (
            "not in manifest; N-column difficulty is empty and A has no rating constant"
            if "NO_SOURCE_CONST" in reason_codes
            else "not in manifest; N-column difficulty is empty; A contains a rating constant only"
        )
        excluded_rows.append(
            {
                "sheet": sheet_name,
                "excel_row": excel_row,
                "title": excluded_title,
                "source_const": float(excluded_const)
                if excluded_const is not None
                else None,
                "difficulty_cell": difficulty_value,
                "legacy_title_match_count": len(matching_legacy),
                "reason_codes": reason_codes,
                "reason": reason,
                "raw_cells": raw_cells,
                "links": links,
                "mapping_cells": {
                    column: values.get(column) for column in _MAP_COLUMNS
                },
            }
        )

    input_hashes = {
        "workbook_sha256": workbook_sha,
        "manifest_sha256": manifest_sha,
        "review_sha256": review_sha,
        "legacy_sha256": legacy_sha,
        "review_base_workbook_sha256": base_sha,
    }
    revision_payload = {
        "reader_contract": _READER_CONTRACT,
        "input_hashes": input_hashes,
    }
    revision = _sha256(
        json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    provenance = {
        "input_hashes": input_hashes,
        "source_workbook_sha256": manifest_source_sha,
        "review_source_workbook_sha256": review_source_sha,
        "review_base_workbook_sha256": base_sha,
        "reviewed_workbook_sha256": workbook_sha,
        "source_tabs": list(_DATA_SHEETS),
        "row_count": len(output_rows),
        "match_counts": method_counts,
        "annotation_counts": {
            "tags": tag_count,
            "tagged_rows": tagged_row_count,
            "notes": note_count,
            "source_notes": source_note_count,
            "links": link_count,
        },
        "excluded_unmapped_rows": excluded_rows,
        "legacy_field_policy": (
            "tags, flags, sss_stars, note, and genre retain legacy normalized values after source-cell checks; "
            "raw_cells preserves unnormalized XLSX values and hyperlink destinations"
        ),
    }
    return {
        "namespace": _NAMESPACE,
        "revision": revision,
        "workbook_sha256": workbook_sha,
        "provenance": provenance,
        "rows": output_rows,
    }


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        _fail("SOURCE_TEXT_INVALID")
    if isinstance(value, (int, float)) and math.isfinite(value):
        value = str(value)
    if not isinstance(value, str):
        _fail("SOURCE_TEXT_INVALID")
    return unicodedata.normalize("NFKC", value).strip()
