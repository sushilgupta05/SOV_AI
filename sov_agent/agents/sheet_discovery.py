"""Agent 1: Sheet Intelligence & Discovery.

Deterministic profile -> one masked LLM call that classifies and ranks every sheet -> deterministic
validation of the LLM output -> heuristic fallback if the LLM is unavailable or returns bad output.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from sov_agent import config
from sov_agent.agents.llm_client import JsonCompletion, LLMUnavailable, default_json_completion
from sov_agent.ingestion.cells import is_empty
from sov_agent.schema.target_schema import TARGET_FIELD_NAMES, normalise
from sov_agent.state import PipelineState, SheetClassification, SheetProfile, require_validated

logger = logging.getLogger(__name__)
STAGE, AGENT = "sheet_discovery", "agent1_sheet_discovery"

_POSITIVE_NAME = ("sov", "location", "locations", "schedule", "property", "properties", "values",
                  "sites", "site")
# Sheets whose name says the rows are NOT current insured property (checked before positive words,
# so "Deleted Locations" is negative even though it contains "locations").
_NEGATIVE_NAME = ("notes", "note", "summary", "instructions", "readme", "lookup", "codes", "cover",
                  "totals", "glossary", "legend", "info", "deleted", "removed", "sold", "disposed",
                  "elsewhere", "excluded", "prior", "previous", "old", "archive", "original",
                  "comparison", "yoy", "disclaimer", "disclaimers", "confidentiality", "questions",
                  "autos", "auto", "vehicles", "vehicle", "trailer", "trailers", "fleet")
_PII_HEADER = ("insured", "name", "address", "street", "email", "e mail", "phone", "tel", "contact",
               "manager", "broker", "adjuster", "owner")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")

SYSTEM_PROMPT = f"""You are an insurance exposure-data analyst working on Property & Casualty Statements of Values (SOVs).
An SOV workbook lists insured properties, one row per location/building, with address, construction, occupancy and insured values.
The target schema has these 17 fields: {", ".join(TARGET_FIELD_NAMES)}.

Classify EVERY sheet you are given:
- "Primary": row-per-property asset data with insured values; the authoritative source to map to the target schema.
- "Secondary": supporting or reference data (code lookups, building details keyed to locations, policy summaries with useful values).
- "Reject": notes, instructions, cover pages, glossaries, disclaimers, totals/summary pivots, empty or near-empty sheets.

Watch out for sheets that look like the property table but must NOT be Primary:
- deleted / removed / sold locations, or locations insured elsewhere (same columns, but not current exposure) -> Secondary;
- the client's original/raw copy, or prior-year / comparison versions of the schedule -> Secondary;
- vehicle, auto, trailer or mobile-equipment schedules (VIN, make, model, mileage) are not property -> Reject or Secondary.

Rules:
- Base your decision on the numeric evidence provided (target_fields_hit, null_value_percentage, total_row_count, header_confidence, deterministic_score, structural_notes) and the sample rows.
- Use only the sheet names you were given. Never invent sheet names.
- Normally exactly one sheet is Primary. Mark more than one Primary only if they clearly hold the same kind of table split across tabs.
- Give 2-4 short reasoning strings per sheet that cite the evidence.
- confidence is a number between 0 and 1.
- Rank all sheets by relevance (1 = most relevant).

Respond with JSON only, in exactly this shape:
{{"sheets": [{{"sheet_name": "...", "classification": "Primary|Secondary|Reject", "confidence": 0.0, "rank": 1, "reasoning": ["..."]}}]}}"""


class _LLMSheet(BaseModel):
    sheet_name: str
    classification: Literal["Primary", "Secondary", "Reject"]
    confidence: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    reasoning: list[str] = Field(min_length=1)


class _LLMResponse(BaseModel):
    sheets: list[_LLMSheet]


# --------------------------------------------------------------------------- entry point
def run_sheet_discovery(state: PipelineState, llm: JsonCompletion | None = None) -> PipelineState:
    require_validated(state, "ingestion")
    scores = {name: deterministic_score(p) for name, p in state.profiles.items()}
    heuristic = {c.sheet_name: c for c in heuristic_classify(state.profiles, scores)}

    ordered = sorted(state.profiles, key=lambda n: scores[n], reverse=True)
    to_llm, overflow = ordered[: config.MAX_SHEETS_TO_LLM], ordered[config.MAX_SHEETS_TO_LLM:]
    for name in overflow:
        heuristic[name].classification = "Reject"
        heuristic[name].reasoning.append(
            f"Not sent to the LLM: workbook has more than {config.MAX_SHEETS_TO_LLM} sheets and this one "
            "ranked low on structural score")
        state.log(STAGE, AGENT, name, "sheet_not_sent_to_llm", heuristic[name].reasoning[-1])

    manifest = _llm_classify(state, to_llm, scores, heuristic, llm)
    if manifest is None:
        manifest = _rank(list(heuristic.values()))
    else:
        manifest = _rank(manifest + [heuristic[n] for n in overflow], keep_llm_order=True)

    _ensure_primary(manifest, scores, state)
    state.manifest = manifest
    state.selected_sheets = [m.sheet_name for m in manifest if m.classification == "Primary"]
    for m in manifest:
        review = " Needs human review." if m.needs_review else ""
        state.log(STAGE, AGENT, m.sheet_name, "sheet_classified",
                  f"{m.classification} (rank {m.rank}, confidence {m.confidence:.2f}, {m.method}): "
                  + "; ".join(m.reasoning) + "." + review)
    if state.selected_sheets:
        state.mark_validated(STAGE)
    else:
        state.add_error(STAGE, "NO_PRIMARY_SHEET", "No sheet could be identified as the main data sheet.")
    return state


# --------------------------------------------------------------------------- deterministic scoring
def _name_hint(name: str) -> float:
    words = set(normalise(name).split())
    if words & set(_NEGATIVE_NAME):
        return 0.0
    if words & set(_POSITIVE_NAME):
        return 1.0
    return 0.5


def _hard_reject_reason(p: SheetProfile) -> str | None:
    if p.n_rows == 0 and not p.columns and p.header_row is None and "Sheet is empty." in p.structural_notes:
        return "Sheet is empty"
    if p.header_row is None:
        return "No table header could be detected"
    if p.n_rows < 2:
        return f"Only {p.n_rows} data row(s)"
    if p.n_cols < 3:
        return f"Only {p.n_cols} column(s)"
    return None


def deterministic_score(p: SheetProfile) -> float:
    if _hard_reject_reason(p):
        return 0.0
    w = config.SHEET_SCORE_WEIGHTS
    score = (
        w["target_fields"] * min(len(p.target_fields_hit) / 8, 1.0)
        + w["completeness"] * (1 - p.null_pct)
        + w["header_confidence"] * p.header_confidence
        + w["row_volume"] * min(p.n_rows / 20, 1.0)
        + w["row_continuity"] * p.row_continuity
        + w["name_hint"] * _name_hint(p.name)
    )
    if p.hidden:
        score -= config.HIDDEN_SHEET_PENALTY
    return round(max(score, 0.0), 4)


def _evidence(p: SheetProfile) -> list[str]:
    reasons = []
    reason = _hard_reject_reason(p)
    if reason:
        return [reason]
    reasons.append(f"Matches {len(p.target_fields_hit)} of 17 target fields")
    reasons.append(f"{p.null_pct:.0%} of cells empty")
    reasons.append(f"{p.n_rows} data rows, header on row {p.header_row} (confidence {p.header_confidence:.2f})")
    if p.hidden:
        reasons.append("Sheet is hidden")
    return reasons


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = {normalise(x) for x in a}, {normalise(x) for x in b}
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def heuristic_classify(profiles: dict[str, SheetProfile], scores: dict[str, float]) -> list[SheetClassification]:
    ordered = sorted(profiles, key=lambda n: scores[n], reverse=True)
    top = ordered[0] if ordered else None
    out = []
    for name in ordered:
        p, s = profiles[name], scores[name]
        reasons = _evidence(p)
        if _hard_reject_reason(p) or s < config.REJECT_SCORE:
            cls, conf = "Reject", round(min(0.95, 1 - s), 2)
            if not _hard_reject_reason(p):
                reasons.append(f"Structural score {s:.2f} is below {config.REJECT_SCORE}")
        elif not p.target_fields_hit:
            cls, conf = "Reject", 0.8
            reasons.append("No column resembles an SOV property field")
        elif name == top and s >= config.PRIMARY_MIN_SCORE:
            cls, conf = "Primary", round(min(0.95, s), 2)
            reasons.append(f"Highest structural score ({s:.2f})")
        else:
            cls, conf = "Secondary", 0.6
            # Same layout as the top sheet is NOT enough to auto-select it: real SOVs keep "Deleted
            # Locations" / "Insured Elsewhere" tabs with identical columns. Minimise false positives;
            # the reviewer can add the sheet if it really is a continuation.
            if top and _jaccard(p.columns, profiles[top].columns) >= config.SAME_LAYOUT_HEADER_JACCARD:
                reasons.append(f"Same column layout as '{top}'; could be a continuation, deleted or "
                               "excluded locations. Reviewer can add it to the selection")
        out.append(SheetClassification(sheet_name=name, classification=cls, confidence=conf,
                                       reasoning=reasons, deterministic_score=s,
                                       method="heuristic_fallback"))
    return out


# --------------------------------------------------------------------------- LLM path
def _json_safe(v: object) -> object:
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return str(v)


def _mask(column: str, value: object) -> object:
    if is_empty(value):
        return None
    value = _json_safe(value)
    if isinstance(value, str):
        if config.MASK_SAMPLES:
            col = normalise(column)
            if any(re.search(rf"\b{k}\b", col) for k in _PII_HEADER):
                return f"<TEXT len={len(value)}>"
            value = _EMAIL_RE.sub("<EMAIL>", value)
            value = _PHONE_RE.sub("<PHONE>", value)
        if len(value) > config.SAMPLE_VALUE_MAX_CHARS:
            value = value[: config.SAMPLE_VALUE_MAX_CHARS] + "…"
    return value


def _sample_rows(state: PipelineState, p: SheetProfile) -> list[dict]:
    df = state.frames.get(p.name)
    if df is None or df.empty:
        return []
    clean = df[~df.index.isin(list(p.row_flags))]
    picks = list(clean.index[: config.SAMPLE_ROWS])
    if len(clean) > 50:
        picks.append(clean.index[len(clean) // 2])
    return [{c: _mask(c, clean.at[r, c]) for c in clean.columns} for r in picks]


def build_payload(state: PipelineState, names: list[str], scores: dict[str, float]) -> list[dict]:
    payload = []
    for name in names:
        p = state.profiles[name]
        payload.append({
            "sheet_name": name,
            "hidden": p.hidden,
            "detected_header_list": p.columns,
            "header_row": p.header_row,
            "header_confidence": round(p.header_confidence, 2),
            "total_row_count": p.n_rows,
            "total_col_count": p.n_cols,
            "null_value_percentage": round(p.null_pct * 100, 1),
            "columns_mostly_empty": [c for c, v in p.col_null_pct.items() if v >= 0.9],
            "target_fields_hit": p.target_fields_hit,
            "numeric_col_ratio": round(p.numeric_col_ratio, 2),
            "deterministic_score": scores[name],
            "structural_notes": p.structural_notes,
            "sample_rows": _sample_rows(state, p),
        })
    return payload


def _llm_classify(state, names, scores, heuristic, llm) -> list[SheetClassification] | None:
    if not names:
        return None
    try:
        complete = llm or default_json_completion()
    except LLMUnavailable as exc:
        state.log(STAGE, AGENT, None, "llm_unavailable",
                  f"LLM not available ({exc}); using the deterministic classifier for all sheets.")
        return None

    user = json.dumps({"sheets": build_payload(state, names, scores)}, ensure_ascii=False, default=str)
    parsed: _LLMResponse | None = None
    for attempt in (1, 2):
        try:
            parsed = _LLMResponse.model_validate_json(complete(SYSTEM_PROMPT, user))
            break
        except LLMUnavailable as exc:
            state.log(STAGE, AGENT, None, "llm_call_failed", f"{exc}; using the deterministic classifier.")
            return None
        except (ValidationError, ValueError) as exc:
            logger.warning("Agent 1 LLM output invalid (attempt %d): %s", attempt, exc)
            state.log(STAGE, AGENT, None, "llm_output_invalid",
                      f"Attempt {attempt}: LLM response did not match the required JSON schema.")
    if parsed is None:
        state.log(STAGE, AGENT, None, "llm_fallback",
                  "LLM output invalid after retry; using the deterministic classifier for all sheets.")
        return None
    return _validate_llm(state, parsed, names, scores, heuristic)


def _validate_llm(state, parsed: _LLMResponse, names, scores, heuristic) -> list[SheetClassification]:
    valid = set(names)
    seen: dict[str, SheetClassification] = {}
    for item in sorted(parsed.sheets, key=lambda s: (s.rank, -s.confidence)):
        if item.sheet_name not in valid:
            state.log(STAGE, AGENT, None, "llm_unknown_sheet_dropped",
                      f"LLM returned a sheet name that does not exist ('{item.sheet_name}'); ignored.")
            continue
        if item.sheet_name in seen:
            continue
        s = scores[item.sheet_name]
        result = SheetClassification(
            sheet_name=item.sheet_name, classification=item.classification,
            confidence=round(item.confidence, 2), reasoning=list(item.reasoning),
            rank=item.rank, deterministic_score=s, method="llm",
        )
        if item.classification == "Primary" and s < config.DISAGREE_LOW_SCORE:
            result.needs_review = True
            result.reasoning.append(f"Check: LLM chose Primary but structural score is only {s:.2f}")
        if item.classification == "Primary" and _name_hint(item.sheet_name) == 0.0:
            result.needs_review = True
            result.reasoning.append("Check: sheet name suggests notes, deleted/excluded or non-property "
                                    "data, yet the LLM chose Primary")
        if item.classification == "Reject" and s >= config.DISAGREE_HIGH_SCORE:
            result.needs_review = True
            result.reasoning.append(f"Check: LLM rejected a sheet with a high structural score ({s:.2f})")
        seen[item.sheet_name] = result

    for name in names:
        if name not in seen:
            fill = heuristic[name]
            fill.reasoning.append("Not classified by the LLM; deterministic classification used")
            seen[name] = fill
            state.log(STAGE, AGENT, name, "llm_missing_sheet_filled",
                      "LLM response omitted this sheet; deterministic classification used.")
    return list(seen.values())


# --------------------------------------------------------------------------- ranking / safety
_CLASS_ORDER = {"Primary": 0, "Secondary": 1, "Reject": 2}


def _rank(items: list[SheetClassification], keep_llm_order: bool = False) -> list[SheetClassification]:
    if keep_llm_order:
        key = lambda m: (m.method != "llm", _CLASS_ORDER[m.classification], m.rank, -m.deterministic_score)
    else:
        key = lambda m: (_CLASS_ORDER[m.classification], -m.deterministic_score)
    ranked = sorted(items, key=key)
    for i, m in enumerate(ranked, start=1):
        m.rank = i
    return ranked


def _ensure_primary(manifest: list[SheetClassification], scores: dict[str, float], state) -> None:
    if any(m.classification == "Primary" for m in manifest) or not manifest:
        return
    best = max(manifest, key=lambda m: scores.get(m.sheet_name, 0))
    if scores.get(best.sheet_name, 0) <= 0:
        return
    best.classification = "Primary"
    best.needs_review = True
    best.reasoning.append("No Primary sheet returned; promoted by highest structural score")
    state.log(STAGE, AGENT, best.sheet_name, "primary_promoted",
              f"No sheet was classified Primary; '{best.sheet_name}' promoted (structural score "
              f"{scores[best.sheet_name]:.2f}) and marked for human review.")
    manifest.sort(key=lambda m: (_CLASS_ORDER[m.classification], m.rank))
    for i, m in enumerate(manifest, start=1):
        m.rank = i
