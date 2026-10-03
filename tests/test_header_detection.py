from sov_agent.pipeline import run_ingestion


def test_clean_header_row_1(fx):
    p = run_ingestion(fx["clean.xlsx"]).profiles["SOV"]
    assert p.header_row == 1
    assert p.n_rows == 30
    assert p.header_confidence >= 0.6


def test_title_block_header_row_6(fx):
    state = run_ingestion(fx["title_block.xlsx"])
    p = state.profiles["Schedule"]
    assert p.header_row == 6
    assert "title/preamble" in p.header_rationale
    assert p.n_rows == 25


def test_two_row_header_combined(fx):
    p = run_ingestion(fx["two_row_header.xlsx"]).profiles["Locations"]
    assert p.header_row == 3
    assert p.header_rows_combined == [2, 3]
    assert "Values - Building" in p.columns
    assert "Values - BI" in p.columns
    assert "Location - City" in p.columns
    assert "Building Value" in p.target_fields_hit
    assert len(p.target_fields_hit) >= 12  # child labels resolve, not the general group term
    assert p.header_merged_ranges


def test_last_row_never_chosen_as_header(fx):
    """Regression: a row with nothing below it must not win on 'contrast'."""
    p = run_ingestion(fx["multi_sheet.xlsx"]).profiles["Lookup"]
    assert p.header_row == 1
    assert p.n_rows == 5


def test_text_heavy_first_data_row_not_header(fx):
    p = run_ingestion(fx["text_heavy.xlsx"]).profiles["Data"]
    assert p.header_row == 3
    assert p.columns[0] == "Loc"


def test_totals_flagged_not_removed(fx):
    state = run_ingestion(fx["totals.xlsx"])
    p, df = state.profiles["SOV"], state.frames["SOV"]
    flags = {row: f for row, f in p.row_flags.items()}
    total_rows = [r for r, f in flags.items() if "total_row" in f]
    subtotal_rows = [r for r, f in flags.items() if "subtotal_row" in f]
    assert total_rows == [15] and subtotal_rows == [14]
    assert 15 in df.index and 14 in df.index  # still present: removal needs human approval


def test_data_merged_cells_not_filled(fx):
    state = run_ingestion(fx["data_merged.xlsx"])
    p, df = state.profiles["SOV"], state.frames["SOV"]
    assert "C5:C7" in p.data_merged_ranges
    assert df.at[5, "City"] == "Austin"
    assert df.at[6, "City"] is None and df.at[7, "City"] is None


def test_duplicate_and_blank_headers(fx):
    state = run_ingestion(fx["dup_blank_headers.xlsx"])
    p = state.profiles["SOV"]
    assert "Value" in p.columns and "Value__2" in p.columns
    assert "Unnamed_Col_F" in p.columns
    assert "Unnamed_Col_G" not in p.columns  # no header and no data -> ignored
    actions = {d.action for d in state.decisions_log}
    assert {"column_renamed_duplicate", "unnamed_columns", "empty_columns_ignored"} <= actions


def test_every_structural_decision_logged(fx):
    state = run_ingestion(fx["title_block.xlsx"])
    assert all(d.rationale for d in state.decisions_log)
    assert any(d.action == "header_detected" for d in state.decisions_log)
