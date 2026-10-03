import json

import pytest

from sov_agent.agents.sheet_discovery import SYSTEM_PROMPT, build_payload, deterministic_score
from sov_agent.errors import StageNotValidatedError
from sov_agent.pipeline import run_ingestion, run_phase1, run_sheet_discovery
from sov_agent.state import PipelineState, override_sheet_selection


def _by_name(state):
    return {m.sheet_name: m for m in state.manifest}


def test_requires_validated_ingestion():
    with pytest.raises(StageNotValidatedError):
        run_sheet_discovery(PipelineState())


def test_heuristic_multi_sheet(fx):
    state = run_phase1(fx["multi_sheet.xlsx"])  # LLM disabled in tests -> heuristic
    m = _by_name(state)
    assert m["Locations"].classification == "Primary"
    assert m["Cover"].classification == "Reject"
    assert m["Empty"].classification == "Reject"
    assert m["Lookup"].classification in {"Secondary", "Reject"}
    assert state.selected_sheets == ["Locations"]
    assert m["Locations"].rank == 1
    assert all(item.reasoning for item in state.manifest)
    assert "sheet_discovery" in state.validated_stages
    assert any(d.action == "llm_unavailable" for d in state.decisions_log)


def test_payload_is_masked_and_small(fx):
    state = run_ingestion(fx["multi_sheet.xlsx"])
    scores = {n: deterministic_score(p) for n, p in state.profiles.items()}
    payload = build_payload(state, ["Locations"], scores)[0]
    assert len(payload["sample_rows"]) <= 5
    for row in payload["sample_rows"]:
        assert row["Street Address"].startswith("<TEXT len=")
    assert isinstance(json.dumps(payload, default=str), str)


def _llm_returning(*responses):
    calls = iter(responses)

    def complete(system, user):
        assert system == SYSTEM_PROMPT
        return next(calls)
    return complete


def test_llm_happy_path(fx):
    resp = json.dumps({"sheets": [
        {"sheet_name": "Locations", "classification": "Primary", "confidence": 0.95, "rank": 1,
         "reasoning": ["Row per location with values"]},
        {"sheet_name": "Lookup", "classification": "Secondary", "confidence": 0.7, "rank": 2,
         "reasoning": ["Code lookup table"]},
        {"sheet_name": "Cover", "classification": "Reject", "confidence": 0.9, "rank": 3,
         "reasoning": ["Notes only"]},
        {"sheet_name": "Empty", "classification": "Reject", "confidence": 0.99, "rank": 4,
         "reasoning": ["Empty"]},
    ]})
    state = run_phase1(fx["multi_sheet.xlsx"], llm=_llm_returning(resp))
    m = _by_name(state)
    assert m["Locations"].method == "llm" and m["Locations"].classification == "Primary"
    assert not m["Locations"].needs_review
    assert state.selected_sheets == ["Locations"]


def test_llm_invalid_json_falls_back(fx):
    state = run_phase1(fx["multi_sheet.xlsx"], llm=_llm_returning("not json", "{\"sheets\": 5}"))
    assert all(m.method == "heuristic_fallback" for m in state.manifest)
    assert state.selected_sheets == ["Locations"]
    assert any(d.action == "llm_fallback" for d in state.decisions_log)


def test_llm_unknown_sheet_dropped_and_missing_filled(fx):
    resp = json.dumps({"sheets": [
        {"sheet_name": "Locations", "classification": "Primary", "confidence": 0.9, "rank": 1,
         "reasoning": ["ok"]},
        {"sheet_name": "Ghost Sheet", "classification": "Primary", "confidence": 0.9, "rank": 2,
         "reasoning": ["invented"]},
    ]})
    state = run_phase1(fx["multi_sheet.xlsx"], llm=_llm_returning(resp))
    names = {m.sheet_name for m in state.manifest}
    assert names == {"Locations", "Lookup", "Cover", "Empty"}
    assert any(d.action == "llm_unknown_sheet_dropped" for d in state.decisions_log)
    assert _by_name(state)["Cover"].method == "heuristic_fallback"


def test_llm_no_primary_promotes_best(fx):
    resp = json.dumps({"sheets": [
        {"sheet_name": n, "classification": "Secondary", "confidence": 0.5, "rank": i + 1,
         "reasoning": ["unsure"]}
        for i, n in enumerate(["Locations", "Lookup", "Cover", "Empty"])
    ]})
    state = run_phase1(fx["multi_sheet.xlsx"], llm=_llm_returning(resp))
    loc = _by_name(state)["Locations"]
    assert loc.classification == "Primary" and loc.needs_review
    assert state.selected_sheets == ["Locations"]


def test_llm_disagreement_flagged(fx):
    resp = json.dumps({"sheets": [
        {"sheet_name": "Cover", "classification": "Primary", "confidence": 0.8, "rank": 1, "reasoning": ["?"]},
        {"sheet_name": "Locations", "classification": "Reject", "confidence": 0.8, "rank": 2, "reasoning": ["?"]},
        {"sheet_name": "Lookup", "classification": "Reject", "confidence": 0.8, "rank": 3, "reasoning": ["?"]},
        {"sheet_name": "Empty", "classification": "Reject", "confidence": 0.8, "rank": 4, "reasoning": ["?"]},
    ]})
    state = run_phase1(fx["multi_sheet.xlsx"], llm=_llm_returning(resp))
    m = _by_name(state)
    assert m["Cover"].needs_review and m["Locations"].needs_review


def test_human_override_logged(fx):
    state = run_phase1(fx["multi_sheet.xlsx"])
    override_sheet_selection(state, ["Lookup"], reviewer="analyst")
    assert state.selected_sheets == ["Lookup"]
    assert _by_name(state)["Locations"].classification == "Secondary"
    assert state.decisions_log[-1].action == "sheet_selection_override"


@pytest.mark.parametrize("name", ["clean.xlsx", "title_block.xlsx", "two_row_header.xlsx",
                                  "text_heavy.xlsx", "totals.xlsx", "data_merged.xlsx",
                                  "dup_blank_headers.xlsx", "zip_text.xlsx", "preamble.csv"])
def test_every_fixture_end_to_end(fx, name):
    state = run_phase1(fx[name])
    assert {"ingestion", "sheet_discovery"} <= state.validated_stages
    assert len(state.selected_sheets) == 1
    state.model_dump_json()  # state is serialisable for the UI / audit
