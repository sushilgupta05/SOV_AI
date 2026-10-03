"""Regression tests on the hackathon sample SOVs in data/ (skipped if the files are absent)."""
import json
from pathlib import Path

import pytest

from sov_agent.pipeline import run_phase1

DATA = Path(__file__).resolve().parents[1] / "data"


def _load(name, **kw):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    return run_phase1(path, **kw)


def _cls(state):
    return {m.sheet_name: m.classification for m in state.manifest}


def test_b4id_title_block_totals_and_notes():
    s = _load("SOV_B4ID.xlsx")
    p = s.profiles["SOV"]
    assert s.selected_sheets == ["SOV"]
    assert p.header_row == 12
    assert p.row_flags[28] == ["total_row"]
    assert all(p.row_flags[r] == ["notes_row"] for r in range(30, 35))
    assert s.frames["SOV"].at[13, "Zip"] == "00802"  # leading zero kept


def test_h6d2_phantom_rows_and_uncached_formulas():
    s = _load("SOV_H6D2.xlsx")
    p = s.profiles["SOV"]
    assert s.selected_sheets == ["SOV"] and p.header_row == 7 and p.n_rows == 20
    assert p.uncached_formula_columns.get("Total") == 19
    assert "Street Address" in p.columns  # header newline collapsed


def test_k4t9_picks_locations_over_client_original_and_boilerplate():
    s = _load("SOV_K4T9.xlsx")
    c = _cls(s)
    assert s.selected_sheets == ["Locations"]
    assert s.profiles["Locations"].header_row == 6
    assert c["Original from Client A"] != "Primary"
    assert c["Confidentiality"] == c["Vendor Disclaimers"] == c["Glossary"] == "Reject"
    assert "Building Value" in s.profiles["Locations"].target_fields_hit  # "Values - Buildings"
    assert s.profiles["Locations"].uncached_formula_columns == {"PD": 34, "TIV": 34}


def test_q8b3_deleted_and_elsewhere_not_selected():
    s = _load("SOV_Q8B3.xlsx")
    c = _cls(s)
    assert s.selected_sheets == ["23-24 Values"]
    assert c["Deleted Locations"] != "Primary" and c["Insured Elsewhere"] != "Primary"
    assert c["All Autos"] == c["Equipment"] == c["Trailer"] == c["Questions"] == "Reject"
    assert s.profiles["Equipment"].header_row is None  # headerless vehicle data is not a table
    assert s.profiles["23-24 Values"].n_rows == 864


def test_q8b3_llm_choosing_deleted_locations_is_flagged():
    sheets = ["23-24 Values", "Deleted Locations", "Insured Elsewhere", "2023 BI Values",
              "All Autos", "Questions", "Trailer", "Equipment"]
    resp = json.dumps({"sheets": [
        {"sheet_name": n, "classification": "Primary" if i < 2 else "Reject", "confidence": 0.8,
         "rank": i + 1, "reasoning": ["mock"]} for i, n in enumerate(sheets)
    ]})
    s = _load("SOV_Q8B3.xlsx", llm=lambda system, user: resp)
    deleted = next(m for m in s.manifest if m.sheet_name == "Deleted Locations")
    assert deleted.needs_review
