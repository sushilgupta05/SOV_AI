"""Shared pipeline state (NFR-7): every agent reads from and writes to one PipelineState."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from sov_agent.errors import StageNotValidatedError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Decision(BaseModel):
    stage: str
    agent: str
    sheet: str | None = None
    action: str
    rationale: str
    timestamp: datetime = Field(default_factory=_now)


class SheetProfile(BaseModel):
    name: str
    hidden: bool = False
    header_row: int | None = None  # 1-based Excel row / CSV line
    header_rows_combined: list[int] = Field(default_factory=list)
    header_confidence: float = 0.0
    header_rationale: str = ""
    columns: list[str] = Field(default_factory=list)
    original_headers: dict[str, str] = Field(default_factory=dict)
    n_rows: int = 0
    n_cols: int = 0
    null_pct: float = 1.0
    col_null_pct: dict[str, float] = Field(default_factory=dict)
    numeric_col_ratio: float = 0.0
    target_fields_hit: list[str] = Field(default_factory=list)
    row_continuity: float = 0.0
    header_merged_ranges: list[str] = Field(default_factory=list)
    data_merged_ranges: list[str] = Field(default_factory=list)
    row_flags: dict[int, list[str]] = Field(default_factory=dict)
    # column -> number of data cells holding a formula with no saved result (they read as empty)
    uncached_formula_columns: dict[str, int] = Field(default_factory=dict)
    structural_notes: list[str] = Field(default_factory=list)


class SheetClassification(BaseModel):
    sheet_name: str
    classification: Literal["Primary", "Secondary", "Reject"]
    confidence: float = Field(ge=0, le=1)
    reasoning: list[str]
    rank: int = 0
    deterministic_score: float = 0.0
    method: Literal["llm", "heuristic_fallback"] = "heuristic_fallback"
    needs_review: bool = False


class PipelineState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    file_meta: dict = Field(default_factory=dict)
    profiles: dict[str, SheetProfile] = Field(default_factory=dict)
    manifest: list[SheetClassification] = Field(default_factory=list)
    selected_sheets: list[str] = Field(default_factory=list)
    validated_stages: set[str] = Field(default_factory=set)
    errors: list[dict] = Field(default_factory=list)
    decisions_log: list[Decision] = Field(default_factory=list)
    # DataFrames are kept out of serialisation; they never leave the process.
    raw_frames: dict[str, pd.DataFrame] = Field(default_factory=dict, exclude=True)
    frames: dict[str, pd.DataFrame] = Field(default_factory=dict, exclude=True)

    def log(self, stage: str, agent: str, sheet: str | None, action: str, rationale: str) -> None:
        self.decisions_log.append(
            Decision(stage=stage, agent=agent, sheet=sheet, action=action, rationale=rationale)
        )

    def mark_validated(self, stage: str) -> None:
        self.validated_stages.add(stage)

    def add_error(self, stage: str, code: str, message: str, sheet: str | None = None) -> None:
        self.errors.append({"stage": stage, "code": code, "message": message, "sheet": sheet})


def require_validated(state: PipelineState, stage: str) -> None:
    if stage not in state.validated_stages:
        raise StageNotValidatedError(
            f"Stage '{stage}' has not been validated; the next agent cannot run on its output."
        )


def override_sheet_selection(state: PipelineState, sheet_names: list[str], reviewer: str) -> PipelineState:
    """Human override of the Primary sheet selection. Logged as a decision."""
    unknown = [s for s in sheet_names if s not in state.profiles]
    if unknown:
        raise ValueError(f"Unknown sheet(s): {unknown}")
    if not sheet_names:
        raise ValueError("At least one sheet must be selected.")
    previous = list(state.selected_sheets)
    state.selected_sheets = list(sheet_names)
    for item in state.manifest:
        if item.sheet_name in sheet_names and item.classification != "Primary":
            item.classification = "Primary"
            item.reasoning.append(f"Set to Primary by reviewer '{reviewer}'")
        elif item.sheet_name not in sheet_names and item.classification == "Primary":
            item.classification = "Secondary"
            item.reasoning.append(f"Demoted to Secondary by reviewer '{reviewer}'")
    state.log(
        "sheet_discovery", f"human:{reviewer}", None, "sheet_selection_override",
        f"Reviewer changed selected sheet(s) from {previous} to {list(sheet_names)}.",
    )
    state.mark_validated("sheet_discovery")
    return state
