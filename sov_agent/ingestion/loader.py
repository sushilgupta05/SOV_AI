"""FR-1 file validation and raw reading of .xlsx/.xlsm/.csv into raw grids.

No cell values are changed here: grids hold exactly what the file contains.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sov_agent import config
from sov_agent.errors import IngestionError
from sov_agent.ingestion.cells import is_empty
from sov_agent.ingestion.formulas import uncached_formulas

logger = logging.getLogger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"
_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")
_CSV_DELIMITERS = ",;\t|"


@dataclass
class MergedRange:
    coord: str  # e.g. "C3:F3"
    min_row: int  # 1-based
    min_col: int  # 1-based
    max_row: int
    max_col: int


@dataclass
class RawSheet:
    name: str
    grid: list[list[object]]  # grid[i] is source row i + 1
    hidden: bool = False
    merged: list[MergedRange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    # (row1, col1) -> formula text, for formulas the file stores without a saved result
    uncached_formulas: dict[tuple[int, int], str] = field(default_factory=dict)


@dataclass
class RawWorkbook:
    file_meta: dict
    sheets: list[RawSheet]


def load_file(source: str | Path | bytes, filename: str | None = None) -> RawWorkbook:
    """Validate and read a file. `source` is a path or raw bytes (e.g. from an upload widget)."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        filename = filename or path.name
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise IngestionError("FILE_NOT_READABLE", f"The file '{filename}' could not be opened.",
                                 str(exc)) from exc
    else:
        data = source
        filename = filename or "upload"

    ext = Path(filename).suffix.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        raise IngestionError(
            "UNSUPPORTED_TYPE",
            f"'{filename}' is not a supported file type. Please upload an .xlsx or .csv file.",
        )
    if len(data) == 0:
        raise IngestionError("EMPTY_FILE", f"'{filename}' is empty (0 bytes).")
    if len(data) > config.MAX_FILE_MB * 1024 * 1024:
        raise IngestionError(
            "TOO_LARGE",
            f"'{filename}' is {len(data) / 1024 / 1024:.1f} MB; the limit is {config.MAX_FILE_MB} MB.",
        )

    file_meta = {
        "name": filename,
        "extension": ext,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }

    if ext == ".csv":
        sheets = [_read_csv(data, filename, file_meta)]
    else:
        sheets = _read_excel(data, filename)
    file_meta["sheet_count"] = len(sheets)
    return RawWorkbook(file_meta=file_meta, sheets=sheets)


# --------------------------------------------------------------------------- Excel
def _read_excel(data: bytes, filename: str) -> list[RawSheet]:
    if data[:4] == _OLE_MAGIC:
        raise IngestionError(
            "PASSWORD_OR_LEGACY",
            f"'{filename}' is password-protected or is an old .xls file. "
            "Please save it as an unprotected .xlsx and upload again.",
        )
    if data[:4] != _ZIP_MAGIC:
        raise IngestionError(
            "CORRUPT_FILE", f"'{filename}' is not a valid Excel workbook or is damaged."
        )

    from openpyxl import load_workbook  # local import keeps CSV path light

    try:
        # read_only=False is required: merged-cell info is unavailable in read-only mode.
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # zipfile.BadZipFile, KeyError, InvalidFileException, ...
        raise IngestionError(
            "CORRUPT_FILE", f"'{filename}' could not be read as an Excel workbook; it may be damaged.",
            repr(exc),
        ) from exc

    formulas = uncached_formulas(data)
    sheets: list[RawSheet] = []
    for chart in getattr(wb, "chartsheets", []):
        logger.info("Skipping chart sheet %s", chart.title)
    for ws in wb.worksheets:
        try:
            raw = _read_worksheet(ws)
            raw.uncached_formulas = formulas.get(ws.title, {})
            sheets.append(raw)
        except Exception as exc:  # never let one bad sheet sink the workbook
            logger.warning("Failed to read sheet %s: %r", ws.title, exc)
            sheets.append(RawSheet(name=ws.title, grid=[], hidden=ws.sheet_state != "visible",
                                   notes=["Sheet could not be read and was treated as empty."]))
    if not sheets:
        raise IngestionError("NO_DATA_FOUND", f"'{filename}' contains no worksheets.")
    return sheets


def _read_worksheet(ws) -> RawSheet:
    rows: list[list[object]] = []
    last_non_empty = 0
    empty_streak = 0
    truncated = False
    for row in ws.iter_rows(values_only=True):
        if any(not is_empty(v) for v in row):
            rows.append(list(row))
            last_non_empty = len(rows)
            empty_streak = 0
        else:
            rows.append(list(row))
            empty_streak += 1
            if empty_streak >= config.EMPTY_ROW_STOP:
                break  # phantom extent: formatting reaches far past the data
        if last_non_empty > config.MAX_ROWS:
            truncated = True
            break

    rows = _trim(rows[:last_non_empty])
    notes: list[str] = []
    if ws.max_row > len(rows) + config.EMPTY_ROW_STOP:
        notes.append(
            f"Sheet reports {ws.max_row:,} used rows but only {len(rows):,} contain data; "
            "trailing empty rows ignored."
        )
    if truncated:
        notes.append(f"Sheet exceeds {config.MAX_ROWS:,} rows; only the first {config.MAX_ROWS:,} were read.")

    merged = [
        MergedRange(str(r.coord), r.min_row, r.min_col, r.max_row, r.max_col)
        for r in ws.merged_cells.ranges
        if r.min_row <= len(rows)
    ]
    return RawSheet(
        name=ws.title,
        grid=rows,
        hidden=ws.sheet_state != "visible",
        merged=merged,
        notes=notes,
        truncated=truncated,
    )


def _trim(rows: list[list[object]]) -> list[list[object]]:
    """Drop trailing empty columns and pad every row to the same width."""
    width = 0
    for row in rows:
        for j in range(len(row) - 1, -1, -1):
            if not is_empty(row[j]):
                width = max(width, j + 1)
                break
    return [(row + [None] * width)[:width] for row in rows]


# --------------------------------------------------------------------------- CSV
def _read_csv(data: bytes, filename: str, file_meta: dict) -> RawSheet:
    if data[:4] in (_ZIP_MAGIC, _OLE_MAGIC):
        raise IngestionError(
            "UNSUPPORTED_TYPE",
            f"'{filename}' looks like an Excel workbook saved with a .csv extension. "
            "Please rename it to .xlsx or export it as a real CSV.",
        )
    if b"\x00" in data[:8192]:
        raise IngestionError("CORRUPT_FILE", f"'{filename}' contains binary data and is not a valid CSV.")

    text, encoding = None, None
    for enc in _CSV_ENCODINGS:
        try:
            text = data.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise IngestionError("CORRUPT_FILE", f"'{filename}' uses an unrecognised text encoding.")

    delimiter = _detect_delimiter(text[:65536])
    file_meta["encoding"] = encoding
    file_meta["delimiter"] = delimiter

    try:
        rows = [list(r) for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    except csv.Error as exc:
        raise IngestionError("CORRUPT_FILE", f"'{filename}' could not be parsed as CSV.", repr(exc)) from exc

    truncated = False
    non_empty = [i for i, r in enumerate(rows) if any(not is_empty(v) for v in r)]
    rows = rows[: non_empty[-1] + 1] if non_empty else []
    if len(rows) > config.MAX_ROWS:
        rows, truncated = rows[: config.MAX_ROWS], True
    rows = [[None if v == "" else v for v in r] for r in rows]
    notes = [f"CSV read with encoding '{encoding}' and delimiter {delimiter!r}."]
    if truncated:
        notes.append(f"File exceeds {config.MAX_ROWS:,} rows; only the first {config.MAX_ROWS:,} were read.")
    return RawSheet(name=Path(filename).stem or "CSV", grid=_trim(rows), notes=notes, truncated=truncated)


def _detect_delimiter(sample: str) -> str:
    """Pick the delimiter whose per-line count is most consistent across the table body.

    csv.Sniffer is unreliable when title lines above the header have a different shape, so each
    candidate is scored as (lines sharing its most common non-zero count) x (that count).
    """
    lines = [ln for ln in sample.splitlines() if ln.strip()][:100]
    best, best_score = ",", 0
    for delim in _CSV_DELIMITERS:
        counts: dict[int, int] = {}
        for ln in lines:
            n = len(next(csv.reader([ln], delimiter=delim), [])) - 1
            if n > 0:
                counts[n] = counts.get(n, 0) + 1
        if not counts:
            continue
        modal, freq = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if freq * modal > best_score:
            best, best_score = delim, freq * modal
    return best
