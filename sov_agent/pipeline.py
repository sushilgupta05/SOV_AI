"""Pipeline stages as pure-ish functions (state in -> state out), ready to wrap as LangGraph nodes."""
from __future__ import annotations

from pathlib import Path

from sov_agent.agents.llm_client import JsonCompletion
from sov_agent.agents.sheet_discovery import run_sheet_discovery
from sov_agent.errors import IngestionError
from sov_agent.ingestion.loader import load_file
from sov_agent.ingestion.structure import build_sheet
from sov_agent.state import PipelineState

__all__ = ["run_ingestion", "run_sheet_discovery", "run_phase1"]


def run_ingestion(source: str | Path | bytes, filename: str | None = None) -> PipelineState:
    """FR-1: validate, read and profile every sheet. Raises IngestionError with a user message."""
    workbook = load_file(source, filename)
    state = PipelineState(file_meta=workbook.file_meta)
    state.log("ingestion", "ingestion_engine", None, "file_loaded",
              f"Loaded '{workbook.file_meta['name']}' ({workbook.file_meta['size_bytes']:,} bytes, "
              f"{len(workbook.sheets)} sheet(s)).")
    for raw in workbook.sheets:
        state.profiles[raw.name] = build_sheet(raw, state)

    tables = [p for p in state.profiles.values() if p.header_row is not None and p.n_rows >= 1]
    if not tables:
        found = "; ".join(
            f"'{p.name}': " + (p.structural_notes[-1] if p.structural_notes else "no table")
            for p in state.profiles.values()
        )
        raise IngestionError(
            "NO_DATA_FOUND",
            "No data table could be found in this file. " + (f"What was found: {found}" if found else ""),
        )
    state.mark_validated("ingestion")
    return state


def run_phase1(source: str | Path | bytes, filename: str | None = None,
               llm: JsonCompletion | None = None) -> PipelineState:
    """FR-1 ingestion followed by Agent 1 sheet discovery."""
    return run_sheet_discovery(run_ingestion(source, filename), llm=llm)
