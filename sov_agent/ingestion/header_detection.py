"""Scored header-row detection (FR-1: header rows at any position).

Each candidate row in the first HEADER_SCAN_ROWS non-empty rows is scored on SOV vocabulary hits,
text ratio, type contrast with the rows below, coverage and uniqueness. A sparse group-header row
with merged cells directly above the winner is combined into two-row headers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sov_agent import config
from sov_agent.ingestion.cells import clean_header_text, is_empty, is_text
from sov_agent.ingestion.loader import MergedRange
from sov_agent.schema.target_schema import header_term_hit, normalise


@dataclass
class RowFeatures:
    idx: int  # 0-based grid index
    filled: int
    coverage: float
    text_ratio: float
    keyword_hits: int
    keyword_score: float
    contrast: float
    uniqueness: float
    below_text_ratio: float
    score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class HeaderResult:
    found: bool
    header_idx: int | None = None  # 0-based index of the (last) header row
    header_rows: list[int] = field(default_factory=list)  # 1-based source rows used
    confidence: float = 0.0
    rationale: str = ""
    header_values: list[str] = field(default_factory=list)  # one per grid column, may be ""
    header_merged: list[MergedRange] = field(default_factory=list)


def _filled(row: list[object]) -> int:
    return sum(1 for v in row if not is_empty(v))


def _text_ratio(row: list[object]) -> float:
    vals = [v for v in row if not is_empty(v)]
    return sum(1 for v in vals if is_text(v)) / len(vals) if vals else 0.0


def merged_lookup(merged: list[MergedRange]) -> dict[tuple[int, int], MergedRange]:
    """(row1, col1) -> range, for every cell covered by a merged range (1-based)."""
    cover: dict[tuple[int, int], MergedRange] = {}
    for m in merged:
        if (m.max_row - m.min_row + 1) * (m.max_col - m.min_col + 1) > 20_000:
            continue  # absurd ranges (whole-column merges) are not useful for headers
        for r in range(m.min_row, m.max_row + 1):
            for c in range(m.min_col, m.max_col + 1):
                cover[(r, c)] = m
    return cover


def _header_value(grid, cover, idx0: int, col0: int) -> object:
    """Cell value with header-region merges unpacked (top-left value copied across the range)."""
    m = cover.get((idx0 + 1, col0 + 1))
    if m is not None:
        return grid[m.min_row - 1][m.min_col - 1]
    return grid[idx0][col0]


def score_rows(grid: list[list[object]]) -> list[RowFeatures]:
    non_empty = [i for i, row in enumerate(grid) if _filled(row) > 0]
    if not non_empty:
        return []
    width = max(_filled(grid[i]) for i in non_empty)
    w = config.HEADER_WEIGHTS
    feats: list[RowFeatures] = []
    for pos, i in enumerate(non_empty[: config.HEADER_SCAN_ROWS]):
        row = grid[i]
        filled = _filled(row)
        if filled < max(2, 0.3 * width):
            continue
        values = [v for v in row if not is_empty(v)]
        text_ratio = _text_ratio(row)
        if text_ratio < config.MIN_HEADER_TEXT_RATIO:
            continue
        hits = [header_term_hit(v) for v in values if is_text(v)]
        matched = [h.target_field or h.synonym for h in hits if h is not None]
        keyword_score = min(len(matched) / 4, 1.0)
        below = non_empty[pos + 1: pos + 1 + config.HEADER_CONTRAST_WINDOW]
        if not below:
            continue  # a header needs at least one data row beneath it
        below_tr = sum(_text_ratio(grid[j]) for j in below) / len(below)
        contrast = min(max(text_ratio - below_tr, 0.0), 1.0)
        uniqueness = len({clean_header_text(v).lower() for v in values}) / filled
        coverage = filled / width
        score = (w["keyword"] * keyword_score + w["text_ratio"] * text_ratio + w["contrast"] * contrast
                 + w["coverage"] * coverage + w["uniqueness"] * uniqueness)
        feats.append(RowFeatures(i, filled, coverage, text_ratio, len(matched), keyword_score, contrast,
                                 uniqueness, below_tr, round(score, 4), matched))
    return feats


def detect_header(grid: list[list[object]], merged: list[MergedRange]) -> HeaderResult:
    feats = score_rows(grid)
    if not feats:
        return HeaderResult(found=False, rationale="No row has enough filled cells to be a header.")

    best_score = max(f.score for f in feats)
    best = next(f for f in feats if f.score >= best_score - config.HEADER_TIE_EPSILON)
    if best.score < config.MIN_HEADER_SCORE:
        return HeaderResult(
            found=False,
            confidence=best.score,
            rationale=(f"No convincing header row: best candidate (row {best.idx + 1}) scored "
                       f"{best.score:.2f}, below the {config.MIN_HEADER_SCORE:.2f} threshold."),
        )

    cover = merged_lookup(merged)
    width = len(grid[0]) if grid else 0
    header_rows = [best.idx]

    parent_idx = best.idx - 1
    if parent_idx >= 0 and _is_group_header(grid, merged, parent_idx, best):
        header_rows = [parent_idx, best.idx]

    values: list[str] = []
    for c in range(width):
        child = clean_header_text(_header_value(grid, cover, best.idx, c))
        if len(header_rows) == 2:
            parent = clean_header_text(_header_value(grid, cover, parent_idx, c))
            # Skip the parent when the child already says it ("Account" over "Account Name").
            redundant = set(normalise(parent).split()) <= set(normalise(child).split())
            if parent and child and not redundant:
                values.append(f"{parent} - {child}")
            else:
                values.append(child or parent)
        else:
            values.append(child)

    header_merged = [m for m in merged
                     if m.min_row <= best.idx + 1 and m.max_row >= header_rows[0] + 1]
    return HeaderResult(
        found=True,
        header_idx=best.idx,
        header_rows=[r + 1 for r in header_rows],
        confidence=round(best.score, 4),
        rationale=_rationale(grid, best, header_rows),
        header_values=values,
        header_merged=header_merged,
    )


def _is_group_header(grid, merged, parent_idx: int, child: RowFeatures) -> bool:
    parent = grid[parent_idx]
    texts = {clean_header_text(v).lower() for v in parent if is_text(v)}
    if len(texts) < 2 or _filled(parent) >= child.filled:
        return False
    parent_row1 = parent_idx + 1
    return any(m.min_row == parent_row1 and m.max_col > m.min_col for m in merged)


def _rationale(grid, best: RowFeatures, header_rows: list[int]) -> str:
    terms = ", ".join(dict.fromkeys(best.matched_terms[:5]))
    parts = [
        f"Row {best.idx + 1} selected (score {best.score:.2f}): "
        f"{round(best.text_ratio * best.filled)} of {best.filled} cells are text, "
        f"{best.keyword_hits} match SOV terms" + (f" ({terms})" if terms else "") + ", "
        f"the rows below are {best.below_text_ratio:.0%} text vs {best.text_ratio:.0%} in the header."
    ]
    first = header_rows[0]
    above = [i for i in range(first) if _filled(grid[i]) > 0]
    if above:
        span = "Row 1 contains" if first == 1 else f"Rows 1-{first} contain"
        parts.append(f"{span} a title/preamble block ({len(above)} non-empty row(s)) "
                     "outside the table.")
    if len(header_rows) == 2:
        parts.append(f"Row {header_rows[0] + 1} is a merged group-header row; its labels were combined "
                     f"with row {header_rows[1] + 1} (e.g. 'Values - Building').")
    return " ".join(parts)
