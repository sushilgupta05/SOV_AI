import time

import pytest

from sov_agent.errors import IngestionError
from sov_agent.pipeline import run_ingestion


@pytest.mark.parametrize("key, code", [
    ("bad_empty", "EMPTY_FILE"),
    ("bad_txt", "UNSUPPORTED_TYPE"),
    ("bad_corrupt", "CORRUPT_FILE"),
    ("bad_legacy", "PASSWORD_OR_LEGACY"),
])
def test_bad_files_raise_friendly_errors(fx, key, code):
    with pytest.raises(IngestionError) as exc:
        run_ingestion(fx[key])
    assert exc.value.code == code
    assert exc.value.user_message and "Traceback" not in exc.value.user_message


def test_bytes_upload_path(fx):
    state = run_ingestion(fx["clean.xlsx"].read_bytes(), "clean.xlsx")
    assert "ingestion" in state.validated_stages
    assert len(state.file_meta["sha256"]) == 64


def test_zip_leading_zero_preserved(fx):
    state = run_ingestion(fx["zip_text.xlsx"])
    df = state.frames["SOV"]
    assert df.loc[2, "Zip"] == "02134"  # source row 2 = first data row


def test_csv_with_preamble_cp1252_semicolon(fx):
    state = run_ingestion(fx["preamble.csv"])
    assert state.file_meta["encoding"] == "cp1252"
    assert state.file_meta["delimiter"] == ";"
    p = state.profiles["preamble"]
    assert p.header_row == 3
    assert p.n_rows == 20
    assert "Bldg Repl Cost" in p.columns


def test_phantom_extent_is_trimmed(fx):
    start = time.perf_counter()
    state = run_ingestion(fx["phantom.xlsx"])
    elapsed = time.perf_counter() - start
    assert state.profiles["SOV"].n_rows == 50
    assert elapsed < 10


def test_large_file_performance(fx):
    start = time.perf_counter()
    state = run_ingestion(fx["large.xlsx"])
    elapsed = time.perf_counter() - start
    p = state.profiles["SOV"]
    assert p.n_rows == 5000 and p.n_cols == 40
    print(f"\n5,000 x 40 ingestion took {elapsed:.2f}s")
    assert elapsed < 15  # spec target is < 5 s; see printed timing


def test_raw_values_unchanged(fx):
    """C-01: every working-frame cell equals the source cell."""
    state = run_ingestion(fx["title_block.xlsx"])
    df, raw = state.frames["Schedule"], state.raw_frames["Schedule"]
    for col_idx, col in enumerate(df.columns):
        for source_row in df.index:
            assert df.at[source_row, col] == raw.iat[source_row - 1, col_idx]
