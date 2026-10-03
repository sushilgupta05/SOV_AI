"""Turn a RawSheet into a working DataFrame + SheetProfile.

Structural parsing only (C-01/C-02): cell values are never changed. Rows that look like totals,
repeated headers or notes are flagged in the profile, not removed. Every decision is logged.
"""
from __future__ import annotations

import re

import pandas as pd

from sov_agent.ingestion.cells import clean_header_text, col_letter, is_empty, is_numeric_like, is_text
from sov_agent.ingestion.header_detection import HeaderResult, detect_header
from sov_agent.ingestion.loader import RawSheet
from sov_agent.schema.target_schema import header_term_hit
from sov_agent.state import PipelineState, SheetProfile

STAGE, AGENT = "ingestion", "ingestion_engine"
_TOTAL_RE = re.compile(r"^(grand\s+)?(sub\s*)?totals?\b", re.IGNORECASE)
_NOTES_MIN_LEN = 40


def build_sheet(raw: RawSheet, state: PipelineState) -> SheetProfile:
    """Profile one sheet, store its frames in `state`, and log every structural decision."""
    profile = SheetProfile(name=raw.name, hidden=raw.hidden, structural_notes=list(raw.notes))
    grid = raw.grid
    state.raw_frames[raw.name] = _raw_frame(grid)

    non_empty_rows = sum(1 for row in grid if any(not is_empty(v) for v in row))
    if non_empty_rows == 0:
        profile.structural_notes.append("Sheet is empty.")
        state.log(STAGE, AGENT, raw.name, "empty_sheet", "Sheet has no non-empty cells; kept for the "
                  "manifest so it can be rejected with a reason rather than silently dropped.")
        return profile

    header = detect_header(grid, raw.merged)
    profile.header_confidence = header.confidence
    profile.header_rationale = header.rationale
    if not header.found:
        profile.n_rows = non_empty_rows
        profile.structural_notes.append("No header row detected; sheet does not look like a data table.")
        state.log(STAGE, AGENT, raw.name, "header_not_found", header.rationale)
        return profile

    profile.header_row = header.header_rows[-1]
    profile.header_rows_combined = header.header_rows if len(header.header_rows) > 1 else []
    state.log(STAGE, AGENT, raw.name, "header_detected", header.rationale)

    _split_merged(raw, header, profile, state)
    columns, keep_idx = _build_columns(raw.name, grid, header, profile, state)
    df = _build_frame(raw.name, grid, header, columns, keep_idx, state)
    state.frames[raw.name] = df
    _report_uncached_formulas(raw, set(df.index), columns, keep_idx, profile, state)

    _flag_rows(df, columns, profile)
    if profile.row_flags:
        counts: dict[str, int] = {}
        for flags in profile.row_flags.values():
            for f in flags:
                counts[f] = counts.get(f, 0) + 1
        summary = ", ".join(f"{n} {k.replace('_', ' ')}(s)" for k, n in counts.items())
        profile.structural_notes.append(f"Flagged rows: {summary}.")
        state.log(STAGE, AGENT, raw.name, "rows_flagged",
                  f"Flagged {summary} (rows {sorted(profile.row_flags)[:10]}). These rows are kept; "
                  "removal requires a reviewed recommendation.")

    _metrics(df, grid, header, profile)
    return profile


# --------------------------------------------------------------------------- helpers
def _raw_frame(grid: list[list[object]]) -> pd.DataFrame:
    width = len(grid[0]) if grid else 0
    return pd.DataFrame(
        grid, columns=[col_letter(c) for c in range(width)],
        index=pd.RangeIndex(1, len(grid) + 1, name="source_row"), dtype=object,
    )


def _split_merged(raw: RawSheet, header: HeaderResult, profile: SheetProfile, state: PipelineState) -> None:
    header_last = header.header_rows[-1]
    profile.header_merged_ranges = [m.coord for m in header.header_merged]
    profile.data_merged_ranges = [m.coord for m in raw.merged if m.max_row > header_last]
    if profile.header_merged_ranges:
        state.log(STAGE, AGENT, raw.name, "header_merges_unpacked",
                  f"{len(profile.header_merged_ranges)} merged range(s) in the header "
                  f"({', '.join(profile.header_merged_ranges[:5])}) were unpacked so every column has a "
                  "label. This changes only header labels, not data.")
    if profile.data_merged_ranges:
        note = (f"{len(profile.data_merged_ranges)} merged cell range(s) in the data region "
                f"(e.g. {profile.data_merged_ranges[0]}); values appear only in the first cell. "
                "Left unfilled per C-02; review recommended.")
        profile.structural_notes.append(note)
        state.log(STAGE, AGENT, raw.name, "data_merges_flagged", note)


def _build_columns(sheet, grid, header: HeaderResult, profile: SheetProfile, state):
    data = grid[header.header_idx + 1:]
    width = len(header.header_values)
    has_data = [any(not is_empty(row[c]) for row in data) for c in range(width)]

    columns: list[str] = []
    keep_idx: list[int] = []
    dropped, unnamed, seen = [], [], {}
    for c, label in enumerate(header.header_values):
        if not label and not has_data[c]:
            dropped.append(col_letter(c))
            continue
        name = label
        if not name:
            name = f"Unnamed_Col_{col_letter(c)}"
            unnamed.append(name)
        raw_text = "" if not label else str(_raw_header_text(grid, header, c))
        key = name.lower()
        if key in seen:
            seen[key] += 1
            new_name = f"{name}__{seen[key]}"
            state.log(STAGE, AGENT, sheet, "column_renamed_duplicate",
                      f"Column {col_letter(c)} header '{name}' duplicates an earlier column; "
                      f"renamed to '{new_name}' internally so both are kept.")
            name = new_name
        else:
            seen[key] = 1
        columns.append(name)
        keep_idx.append(c)
        profile.original_headers[name] = raw_text

    if dropped:
        state.log(STAGE, AGENT, sheet, "empty_columns_ignored",
                  f"{len(dropped)} column(s) with no header and no data ignored ({', '.join(dropped[:10])}).")
    if unnamed:
        state.log(STAGE, AGENT, sheet, "unnamed_columns",
                  f"{len(unnamed)} column(s) contain data but no header; named {', '.join(unnamed[:10])}.")
    profile.columns = columns
    return columns, keep_idx


def _raw_header_text(grid, header: HeaderResult, c: int) -> str:
    parts = [clean_header_text(grid[r - 1][c]) for r in header.header_rows]
    return " | ".join(p for p in parts if p)


def _build_frame(sheet, grid, header: HeaderResult, columns, keep_idx, state) -> pd.DataFrame:
    rows, index, blank = [], [], 0
    for i in range(header.header_idx + 1, len(grid)):
        values = [grid[i][c] for c in keep_idx]
        if all(is_empty(v) for v in values):
            blank += 1
            continue
        rows.append(values)
        index.append(i + 1)
    if blank:
        state.log(STAGE, AGENT, sheet, "blank_rows_skipped",
                  f"{blank} completely empty row(s) inside the table were skipped; no values were lost.")
    return pd.DataFrame(rows, columns=columns, index=pd.Index(index, name="source_row", dtype="int64"),
                        dtype=object)


def _report_uncached_formulas(raw: RawSheet, table_rows: set[int], columns, keep_idx,
                              profile: SheetProfile, state: PipelineState) -> None:
    """Report formula cells inside the table that have no saved result (they read as empty)."""
    if not raw.uncached_formulas:
        return
    name_by_col1 = {c + 1: name for c, name in zip(keep_idx, columns)}
    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    for (row1, col1), formula in raw.uncached_formulas.items():
        if row1 not in table_rows or col1 not in name_by_col1:
            continue
        name = name_by_col1[col1]
        counts[name] = counts.get(name, 0) + 1
        if formula.startswith("="):
            examples.setdefault(name, f"{col_letter(col1 - 1)}{row1}: {formula}")
    if not counts:
        return
    profile.uncached_formula_columns = counts
    detail = "; ".join(f"'{c}' ({n} cells" + (f", e.g. {examples[c]}" if c in examples else "") + ")"
                       for c, n in counts.items())
    note = (f"Formula cells with no saved result read as empty: {detail}. This is a file artefact "
            "(saved without recalculation), not missing client data.")
    profile.structural_notes.append(note)
    state.log(STAGE, AGENT, raw.name, "uncached_formulas_detected",
              note + " Values were not computed; re-saving the file in Excel, or an approved "
              "recalculation step, would restore them.")


def _flag_rows(df: pd.DataFrame, columns: list[str], profile: SheetProfile) -> None:
    header_norm = [c.lower() for c in columns]
    min_repeat = max(2, len(columns) // 2)
    for source_row, values in zip(df.index, df.itertuples(index=False, name=None)):
        flags: list[str] = []
        filled = [(j, v) for j, v in enumerate(values) if not is_empty(v)]
        lead = [str(v).strip() for _, v in filled[:3]]
        if any(_TOTAL_RE.match(s) for s in lead):
            flags.append("subtotal_row" if any(re.match(r"^sub\s*total", s, re.I) for s in lead) else "total_row")
        matches = sum(1 for j, v in filled if clean_header_text(v).lower() == header_norm[j])
        if len(filled) >= min_repeat and matches == len(filled):
            flags.append("repeated_header_row")
        if len(filled) == 1 and is_text(filled[0][1]) and len(str(filled[0][1])) >= _NOTES_MIN_LEN \
                and len(columns) > 2:
            flags.append("notes_row")
        if flags:
            profile.row_flags[int(source_row)] = flags


def _metrics(df: pd.DataFrame, grid, header: HeaderResult, profile: SheetProfile) -> None:
    profile.n_rows = len(df)
    profile.n_cols = len(df.columns)
    if profile.n_rows == 0 or profile.n_cols == 0:
        profile.null_pct = 1.0
        profile.structural_notes.append("Header found but no data rows below it.")
        return

    null_mask = df.map(is_empty)
    profile.null_pct = round(float(null_mask.values.mean()), 4)
    profile.col_null_pct = {c: round(float(null_mask[c].mean()), 4) for c in df.columns}

    numeric_cols = 0
    for c in df.columns:
        vals = [v for v in df[c] if not is_empty(v)]
        if vals and sum(1 for v in vals if is_numeric_like(v)) / len(vals) >= 0.8:
            numeric_cols += 1
    profile.numeric_col_ratio = round(numeric_cols / profile.n_cols, 4)

    hits = []
    for c in df.columns:
        # For combined two-row headers ("Values - Building") the child label is the specific one.
        h = None
        if profile.header_rows_combined and " - " in c:
            h = header_term_hit(c.rsplit(" - ", 1)[1])
        if not (h and h.target_field):
            h = header_term_hit(c)
        if h and h.target_field and h.target_field not in hits:
            hits.append(h.target_field)
    profile.target_fields_hit = hits

    longest = run = 0
    for i in range(header.header_idx + 1, len(grid)):
        if any(not is_empty(v) for v in grid[i]):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    profile.row_continuity = round(min(longest / profile.n_rows, 1.0), 4)
