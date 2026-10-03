"""Find formula cells that have no saved (cached) result.

openpyxl's data_only=True returns a formula's last *saved* result. Files re-saved by tools that do
not recalculate (e.g. anonymisers) store the formula with no result, so the cell reads as empty.
That is not missing client data, so we detect it and report it instead of letting it look like a
null. Detection scans the sheet XML directly, avoiding a second full workbook load.
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_CELL_RE = re.compile(r"<c\s[^>]*?r=\"([A-Z]+)(\d+)\"[^>]*?(?:/>|>(.*?)</c>)", re.DOTALL)
_FORMULA_RE = re.compile(r"<f(?:\s[^>]*)?(?:/>|>(.*?)</f>)", re.DOTALL)
_VALUE_RE = re.compile(r"<v>[^<]+</v>")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def uncached_formulas(data: bytes) -> dict[str, dict[tuple[int, int], str]]:
    """sheet name -> {(row1, col1): formula text or '(shared formula)'} for formulas with no result.

    Best effort: any parsing problem returns {} rather than failing ingestion.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", _NS)}
            out: dict[str, dict[tuple[int, int], str]] = {}
            for sheet in wb.findall("m:sheets/m:sheet", _NS):
                target = targets.get(sheet.get(f"{{{_NS['r']}}}id"), "")
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
                    posixpath.join("xl", target))
                if path not in zf.namelist():
                    continue
                xml = zf.read(path).decode("utf-8", errors="replace")
                if "<f" not in xml:
                    continue
                found: dict[tuple[int, int], str] = {}
                for col, row, body in _CELL_RE.findall(xml):
                    if not body or "<f" not in body or _VALUE_RE.search(body):
                        continue
                    f = _FORMULA_RE.search(body)
                    text = (f.group(1) or "").strip() if f else ""
                    found[(int(row), _col_index(col))] = f"={text}" if text else "(shared formula)"
                if found:
                    out[sheet.get("name")] = found
            return out
    except Exception:  # noqa: BLE001 - detection must never break ingestion
        return {}
