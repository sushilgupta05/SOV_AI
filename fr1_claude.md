# System Context
You are an expert Python developer and AI engineer building the first phase of an **Agentic SOV Cleansing & Intelligence System** for a Property & Casualty insurance hackathon (24 hours, team of 2–4).

A Statement of Values (SOV) is an Excel/CSV file listing every insured property for a client. Clients submit SOVs in inconsistent formats: multiple tabs, title blocks above the header, headers on any row, merged cells, multi-row headers, totals rows, notes sheets, and hidden sheets.

Your task: implement **FR-1 (File Ingestion)** and **Agent 1 (Sheet Intelligence and Discovery Agent)**, plus the **shared pipeline state** that all later agents will use.

Later phases (not in scope now, but your design must support them):
- Agent 2: Schema Mapping (map raw headers to 17 target fields)
- Agent 3: Data Quality + Recommendations
- Human approval UI
- Agent 4: Controlled Transformation + export of `Cleaned_SOV.xlsx` + audit log

---

# Hard Rules (from the problem statement, apply to everything you write)

| ID | Rule | What it means for this phase |
|---|---|---|
| C-01 | No auto-transformation | Ingestion may *parse structure* but must never change cell values. Anything that alters or removes data (e.g., dropping a totals row) is only **flagged** here; it becomes a recommendation for a human later. |
| C-02 | No hallucinated data | Never fill, interpolate or invent values. Merged cells in the **data** region are NOT forward-filled. |
| C-05 / NFR-6 | Every decision has a rationale | Every structural decision (header row chosen, column renamed for uniqueness, sheet rejected, row flagged) is written to `state.decisions_log` with a human-readable rationale. No silent drops. |
| NFR-4 | Error handling | Never crash on malformed input. Return a user-readable error for any unsupported or broken file. |
| NFR-7 | Agent state sharing | All stages read/write one structured state object. A stage may only run if the previous stage marked itself validated. |
| Privacy | Secure data handling | No hardcoded API keys (use env vars via `python-dotenv`, ship `.env.example`, `.env` in `.gitignore`). Mask PII-like values before sending anything to an LLM. Never log raw cell data. |
| FR-1 | Scale | Files up to 5,000 rows must load without timeout or memory failure. |

---

# Architecture: Hybrid Deterministic + LLM

1. **Deterministic Python (openpyxl + pandas)** loads the file, unpacks the structure, detects the header row, and computes a numeric profile of every sheet.
2. **LLM (Agent 1)** receives only a compact, masked JSON summary of all sheets (metadata + up to 5 sample rows each) in **one call**. It classifies and ranks the sheets and explains why.
3. **Deterministic validation** checks the LLM output. If the LLM call fails, times out or returns invalid output, a **heuristic fallback classifier** produces the manifest so the pipeline never stops.

Never send full DataFrames to the LLM.

Write every stage as a pure function `(state) -> state` so it can be wrapped as a LangGraph node later.

---

# Tech Stack
- Python 3.11+
- `openpyxl` (Excel reading; needed for merged cells and hidden sheets)
- `pandas`, `pydantic` v2
- `openai` (OpenAI or Azure OpenAI), `python-dotenv`
- `rapidfuzz` (light normalisation help; Agent 2 will use it heavily)
- `streamlit` (minimal upload UI)
- `pytest`

LLM provider and model come from env vars: `LLM_PROVIDER` (`openai` | `azure`), `LLM_MODEL` (default `gpt-4o`), `OPENAI_API_KEY` / `AZURE_OPENAI_*`.

---

# Project Layout
```
sov_agent/
  config.py                  # thresholds and limits (all tunable constants live here)
  errors.py                  # IngestionError(code, user_message)
  state.py                   # PipelineState + Pydantic models below
  schema/target_schema.py    # 17 target fields, types, synonym vocabulary (shared with Agent 2)
  ingestion/loader.py        # file validation + reading xlsx/csv into raw grids
  ingestion/header_detection.py
  ingestion/structure.py     # merged cells, trimming, column cleanup, row flags, SheetProfile
  agents/llm_client.py       # thin provider-agnostic wrapper, JSON mode, timeout, 1 retry
  agents/sheet_discovery.py  # Agent 1: payload builder, masking, LLM call, validation, fallback
  pipeline.py                # run_ingestion(path) and run_sheet_discovery(state)
app.py                       # minimal Streamlit UI for FR-1
tests/fixtures/make_fixtures.py
tests/test_loader.py
tests/test_header_detection.py
tests/test_sheet_discovery.py
requirements.txt
.env.example
```

---

# 1. Shared State (`state.py`)

The state is JSON-serialisable metadata plus DataFrames held in excluded fields.

```python
class Decision(BaseModel):
    stage: str               # "ingestion" | "sheet_discovery" | ...
    agent: str
    sheet: str | None
    action: str              # e.g. "header_detected", "column_renamed_duplicate", "row_flagged_total"
    rationale: str           # plain-English explanation
    timestamp: datetime

class SheetProfile(BaseModel):
    name: str
    hidden: bool
    header_row: int | None              # 1-based Excel row / CSV line number
    header_rows_combined: list[int]     # e.g. [3, 4] for a two-row header
    header_confidence: float            # 0..1
    header_rationale: str
    columns: list[str]                  # cleaned header text (whitespace collapsed; no case changes)
    original_headers: dict[str, str]    # cleaned -> raw text
    n_rows: int                         # data rows below the header
    n_cols: int
    null_pct: float                     # whole data region
    col_null_pct: dict[str, float]
    numeric_col_ratio: float
    target_fields_hit: list[str]        # target fields whose synonyms appear in headers
    row_continuity: float               # longest run of non-empty rows / n_rows
    header_merged_ranges: list[str]
    data_merged_ranges: list[str]       # NOT filled; Agent 3 turns these into recommendations
    row_flags: dict[int, list[str]]     # source_row -> ["total_row", "subtotal_row", ...]
    structural_notes: list[str]

class SheetClassification(BaseModel):
    sheet_name: str
    classification: Literal["Primary", "Secondary", "Reject"]
    confidence: float = Field(ge=0, le=1)
    reasoning: list[str]                # 2–4 short evidence-based strings
    rank: int                           # 1 = most relevant
    deterministic_score: float
    method: Literal["llm", "heuristic_fallback"]
    needs_review: bool = False

class PipelineState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    run_id: str
    file_meta: dict                     # name, extension, size_bytes, sha256, sheet_count
    profiles: dict[str, SheetProfile] = {}
    manifest: list[SheetClassification] = []
    selected_sheets: list[str] = []     # Primary sheet(s); Agent 2 reads this
    validated_stages: set[str] = set()
    errors: list[dict] = []
    decisions_log: list[Decision] = []
    raw_frames: dict[str, pd.DataFrame] = Field(default_factory=dict, exclude=True)  # untouched grid
    frames: dict[str, pd.DataFrame] = Field(default_factory=dict, exclude=True)      # header applied
```

Helpers:
- `state.log(stage, agent, sheet, action, rationale)` appends a `Decision`.
- `state.mark_validated(stage)` records that a stage finished and checked its output.
- `require_validated(state, stage)` raises a clear error if a later stage runs before `stage` is validated.
- `override_sheet_selection(state, sheet_names, reviewer)` lets the human change the Primary selection and logs the change.

**DataFrame conventions** for `frames[sheet]`:
- Columns = cleaned header names.
- Index named `source_row` = the original 1-based Excel row number. This is needed for the audit log and per-row anomaly flags.
- All values are kept as raw objects (`dtype=object`). No type casting happens in this phase.

---

# 2. Target Schema Vocabulary (`schema/target_schema.py`)

Define the 17 target fields (exact, case-sensitive names) with type and synonyms. Agent 2 will reuse this module, so the header detector can already use the synonyms now.

| Field | Type | Starter synonyms |
|---|---|---|
| Reference | str | ref, loc, loc #, location no, location number, site id, property id, bldg #, building no |
| Address | str | address, street, street address, addr, address 1, location address |
| City | str | city, town, municipality |
| State | str | state, st, province, region |
| Zip | int | zip, zip code, postal code, postcode, postal |
| County | str | county, parish |
| Country | str | country, ctry, nation |
| Building Value | float | building value, bldg value, building, bldg, building limit, replacement cost, repl cost, bldg repl cost new, building tiv |
| Contents | float | contents, cont, bpp, business personal property, personal property |
| BI | float | bi, business income, business interruption, bi/ee, loss of rents, rental income, time element |
| Occupancy | str | occupancy, occ, occupancy type, use, property use |
| Construction | str | construction, const, constr, construction type, iso class, building material |
| Storeys | int | stories, storeys, floors, # of stories, no of floors, num stories |
| Number of Buildings | int | number of buildings, # bldgs, no of buildings, num buildings, building count, bldg count |
| Year Built | int | year built, yr built, yr blt, yob, year of construction, built |
| Fire Sprinklers (Y/N) | str | sprinklers, sprinklered, sprinkler, spr, fire prot, fire protection |
| Other | float | other, other values, other tiv, misc, outdoor property, equipment |

Also define `GENERAL_SOV_TERMS`. These words indicate a header row even though they are not target fields: tiv, total insured value, total value, values, limit, deductible, flood, eq, earthquake, named storm, wind, latitude, longitude, lat, long, policy, insured, premium, sq ft, square footage, area, protection class.

**Matching rule (`header_term_hits(cell)`):**
1. Normalise the cell: lowercase, replace punctuation with spaces, collapse whitespace.
2. Synonyms of 3 characters or fewer (e.g., `st`, `bi`, `occ`) count only on an **exact** whole-cell match.
3. Longer synonyms count on a whole-word substring match.
4. Return the matched target field (if any) and whether any general term matched.

---

# 3. FR-1: Ingestion (`ingestion/loader.py`, `structure.py`, `header_detection.py`)

## 3.1 File validation
Raise `IngestionError(code, user_message)` with a friendly message. The UI shows `user_message`; the full exception goes to the internal log only.

| Situation | Code | Detection |
|---|---|---|
| Extension not `.xlsx`, `.xlsm` or `.csv` | `UNSUPPORTED_TYPE` | extension check |
| Empty file (0 bytes) | `EMPTY_FILE` | size |
| Larger than `MAX_FILE_MB` (default 25) | `TOO_LARGE` | size |
| `.xlsx` whose bytes are an OLE container (`D0 CF 11 E0`) | `PASSWORD_OR_LEGACY` | magic bytes. Message: "This file is password-protected or is an old .xls file. Please save it as an unprotected .xlsx and upload again." |
| `.xlsx` that is not a valid zip / openpyxl fails | `CORRUPT_FILE` | magic bytes `PK\x03\x04` + try/except |
| No sheet contains a detectable table | `NO_DATA_FOUND` | after profiling |

Record `sha256` of the file in `file_meta` for traceability.

## 3.2 Reading Excel
- Use `openpyxl.load_workbook(path, data_only=True)` so formulas return their cached values. Do **not** use `read_only=True`, because merged-cell info is unavailable in that mode.
- Iterate `wb.worksheets` only. Chart sheets are skipped with a structural note.
- Record `hidden = ws.sheet_state != "visible"`.
- Build a raw grid using `iter_rows(values_only=True)`. Keep raw Python values; do not convert types.
- **Phantom extent guard:** some sheets report ~1,048,576 rows because of formatting. Trim trailing all-empty rows and columns. Stop scanning after `EMPTY_ROW_STOP` (default 500) consecutive empty rows past the last non-empty row.
- Hard guard: if a sheet exceeds `MAX_ROWS` (default 50,000) real rows, profile it, then add a note and an error entry rather than crashing.
- **Ghost sheets** (0 non-empty cells) are **not deleted**. They get a profile with `n_rows=0` and will be classified `Reject` with the reason "Sheet is empty".

## 3.3 Reading CSV
- Do **not** use plain `pd.read_csv`. It fails with "Error tokenizing data" when title lines above the header have fewer fields than the rows below.
- Read with the `csv` module:
  - Try encodings in this order: `utf-8-sig`, `utf-8`, `cp1252`. Record the one used.
  - Detect the delimiter with `csv.Sniffer` on the first 64 KB. Fall back to `,`.
  - Pad ragged rows to the maximum width.
- Treat the CSV as a workbook with one sheet, named after the file stem. It has no merged cells and no hidden flag.
- All CSV values are strings. Everything downstream must use the "is text" definition below, not `isinstance(v, str)`.

## 3.4 Merged cells (Excel)
1. Collect `ws.merged_cells.ranges`.
2. After the header row is detected (3.5), split the ranges into **header-region** ranges (they touch header rows) and **data-region** ranges.
3. **Header region:** copy the top-left value across the merged range in the working grid. This is structural parsing, not data change. Log it.
4. **Data region:** do **not** fill. Store the ranges in `profile.data_merged_ranges` and add a structural note, e.g. "12 merged cell ranges in data region (e.g. C14:C16); values appear only in the first cell. Left unfilled per C-02; review recommended."

## 3.5 Header detection (scored, not just "density spike")

Helper `is_text(v)` returns True if `v` is a non-empty string that is **not** any of:
- numeric after stripping `$ € £ , % ( )` and spaces
- a date (common formats)
- a boolean literal

Scan the first `HEADER_SCAN_ROWS` (default 30) non-empty rows. Let `W` = the widest row width in the sheet. For each candidate row `r`:

| Feature | Definition |
|---|---|
| `filled` | non-empty cells in `r` |
| `coverage` | `filled / W` |
| `text_ratio` | share of filled cells where `is_text` |
| `keyword_score` | `min(hits / 4, 1)`, where `hits` = cells matching a target synonym or general SOV term |
| `contrast` | `clip(text_ratio(r) - mean text_ratio of the next 10 non-empty rows, 0, 1)` |
| `uniqueness` | distinct non-empty values / `filled` |

Rules:
- Skip `r` if `filled < max(2, 0.3 * W)`. This removes title rows like "Statement of Values – Client X".
- `score = 0.30*keyword_score + 0.20*text_ratio + 0.20*contrast + 0.15*coverage + 0.15*uniqueness`
- Choose the highest score. If scores are within 0.02, choose the earliest row.
- This works when the header is on row 1 (no rows above are needed).
- `header_confidence = score`.
- If the best score is below `MIN_HEADER_SCORE` (default 0.40), set `header_row=None` and add a note. The sheet will most likely be rejected.
- `header_rationale` must cite the evidence, e.g. *"Row 4 selected: 15 of 17 cells are text, 9 match SOV terms (Address, Year Built, TIV…), the 10 rows below are mostly numeric in 8 columns. Rows 1–3 contain a title block."*

**Two-row headers.** If the row directly above the chosen row (`r-1`) meets all of the following:
- it has at least 2 distinct text values,
- it is sparser than `r`,
- it contains header-region merged ranges spanning several columns of `r` (e.g. a merged "Values" above "Building | Contents | BI"),

then combine the two rows. Each column name becomes `"{parent} - {child}"`. If the parent equals the child, or one side is empty, use the non-empty one. Set `header_rows_combined=[r-1, r]` and explain this in the rationale.

## 3.6 Building the working DataFrame
After the header is fixed:
1. **Column names:** use the cleaned header text (strip, collapse whitespace and newlines). Keep raw text in `original_headers`.
2. **Blank header with data below:** name the column `Unnamed_Col_{ExcelLetter}` and log it.
3. **Duplicate names:** add a suffix `__2`, `__3` and log it.
4. **Columns with no header and no data:** drop them and log the count. No information is lost.
5. **Columns with a header but no data:** **keep** them. They matter for null-handling tests.
6. **Fully empty rows inside the data region:** exclude them from `frames` and log the count. They remain in `raw_frames`.
7. **Rows are never deleted for any other reason.** Instead they are flagged in `profile.row_flags[source_row]`:
   - `total_row` / `subtotal_row`: any of the first 3 non-empty cells matches `^(grand\s+)?(sub\s*)?totals?\b` (case-insensitive).
   - `repeated_header_row`: the row's values equal the header (happens when pages are pasted together).
   - `notes_row`: a single non-empty long text cell after the data ends.
8. Set the index to `source_row` (1-based original row). Keep `dtype=object`. Store the result in `state.frames[sheet]`. Store the untouched grid in `state.raw_frames[sheet]`.

## 3.7 Profile metrics
Compute every field of `SheetProfile`:
- `null_pct` and `col_null_pct` over the data region. Empty string or whitespace counts as null.
- `numeric_col_ratio`: share of columns where at least 80% of non-null values are numeric after stripping currency symbols and commas.
- `target_fields_hit`: distinct target fields matched by the headers.
- `row_continuity`: longest run of non-empty rows divided by `n_rows`.

## 3.8 Ingestion validation gate
Mark `"ingestion"` validated only if at least one sheet has `header_row` set and `n_rows >= 1`. Otherwise raise `NO_DATA_FOUND` with a user message listing what was found in each sheet.

---

# 4. Agent 1: Sheet Intelligence & Discovery (`agents/sheet_discovery.py`)

Runs only after `require_validated(state, "ingestion")`.

## 4.1 Deterministic relevance score (always computed)

```
deterministic_score =
    0.35 * min(len(target_fields_hit) / 8, 1)
  + 0.20 * (1 - null_pct)
  + 0.15 * header_confidence
  + 0.15 * min(n_rows / 20, 1)
  + 0.10 * row_continuity
  + 0.05 * name_hint
```

`name_hint` uses the sheet name:
- `1` if it contains sov, location, schedule, property, values, or sites
- `0` if it contains notes, summary, instructions, readme, lookup, codes, cover, or totals
- `0.5` otherwise

Hidden sheets get −0.10.

Score 0 (and Reject) for sheets with `header_row is None`, `n_rows < 2` or `n_cols < 3`.

## 4.2 Heuristic fallback classifier
Used when the LLM is unavailable, and as a sanity check on the LLM:
- **Reject** if score < 0.35 (or the hard conditions above).
- **Primary** for the top-scoring sheet if its score ≥ 0.55. Additional sheets are also Primary if their score is within 0.10 of the top **and** their header overlap with the top sheet (Jaccard on normalised names) is ≥ 0.7 (same table split across tabs).
- **Secondary** for everything else.
- Reasoning strings are generated from the numbers, e.g. `"Matches 11 of 17 target fields"`, `"62% of cells empty"`, `"Only 3 data rows"`.

## 4.3 LLM payload (one call for all sheets)
Send at most 20 sheets, ordered by deterministic score. Any others are Reject via the heuristic, with a logged reason. Per sheet:

```json
{
  "sheet_name": "Location Schedule",
  "hidden": false,
  "detected_header_list": ["Loc #", "Street Address", "Bldg Repl Cost", "..."],
  "header_row": 4,
  "header_confidence": 0.86,
  "total_row_count": 412,
  "total_col_count": 23,
  "null_value_percentage": 7.4,
  "columns_mostly_empty": ["Flood Zone"],
  "target_fields_hit": ["Reference", "Address", "Building Value", "Year Built"],
  "numeric_col_ratio": 0.43,
  "deterministic_score": 0.81,
  "structural_notes": ["2 total rows flagged", "Title block in rows 1-3"],
  "sample_rows": [ ...up to 5 masked rows as {column: value} ... ]
}
```

**Sample rows:**
- Take the first 5 data rows that are not flagged, plus 1 from the middle if `n_rows > 50`.
- Truncate each value to 40 characters.

**Masking (`MASK_SAMPLES=true` by default):**
- In columns whose header matches insured, name, address, street, email, phone, contact, manager, broker or adjuster, replace values with `"<TEXT len=N>"`.
- In any cell, replace email and phone patterns.
- Keep numeric values, codes and short categorical values. They are needed for classification and are not PII.

## 4.4 LLM call
- Use temperature 0, JSON mode / structured output, a 30-second timeout, and 1 retry on invalid JSON.

**System prompt (summary):**
- You are an insurance exposure-data analyst.
- Explain what an SOV is and list the 17 target fields.
- Define the classes:
  - **Primary:** row-per-property asset data with values.
  - **Secondary:** reference or supporting tables, e.g. code lookups, building details keyed to locations, a policy summary with useful values.
  - **Reject:** notes, instructions, cover pages, totals/summary pivots, empty or near-empty sheets.
- Use the provided numeric evidence. Do not invent sheet names. Give 2–4 short reasoning strings that cite evidence. Rank all sheets.

**Required response:**
```json
{
  "sheets": [
    {
      "sheet_name": "SOV_Data",
      "classification": "Primary",
      "confidence": 0.94,
      "rank": 1,
      "reasoning": ["Consistent tabular structure", "High header confidence", "Low null ratio"]
    }
  ]
}
```
Each item is a superset of the spec example (`sheet_name`, `classification`, `confidence`, `reasoning`).

## 4.5 Validate the LLM output (anti-hallucination)
1. Parse the response with Pydantic. On failure, retry once, then use the heuristic fallback for all sheets (`method="heuristic_fallback"`) and log why.
2. If a returned `sheet_name` is not a real sheet, drop it and log it.
3. If a real sheet is missing from the response, fill it in from the heuristic.
4. Normalise ranks to 1..n.
5. If no sheet is Primary, promote the highest deterministic score to Primary with `needs_review=True`. Add the reasoning "No Primary returned by LLM; promoted by highest structural score".
6. **Disagreement check** (set `needs_review=True` and add a reason):
   - the LLM marks Primary a sheet with deterministic score < 0.35, or
   - the LLM rejects a sheet with deterministic score ≥ 0.70.
7. Fill `deterministic_score` and `method="llm"` on each item.

## 4.6 Outputs
- `state.manifest`: the ranked list of `SheetClassification`.
- `state.selected_sheets`: all Primary sheet names in rank order.
- One `Decision` per sheet with its reasoning.
- Mark `"sheet_discovery"` validated only if `selected_sheets` is non-empty.

---

# 5. Minimal UI (`app.py`, Streamlit)
Keep it functional, not pretty.
1. File uploader (`.xlsx`, `.xlsm`, `.csv`). Errors are shown with `st.error(user_message)`.
2. Ranked manifest table: rank, sheet, classification, confidence, method, a needs-review badge, and reasoning.
3. For each sheet: detected header row, header rationale, structural notes.
4. A preview of the first 10 rows of the selected sheet, with `source_row` shown.
5. A multiselect to override the Primary sheet(s). It calls `override_sheet_selection` and the change is logged.
6. An expander showing `state.decisions_log`.

---

# 6. Tests (`tests/`)
`make_fixtures.py` generates these workbooks with openpyxl. Each test asserts the expected result.

| Fixture | Expected |
|---|---|
| Clean SOV, header on row 1 | `header_row=1`, sheet Primary |
| Title block in rows 1–5, header on row 6 | `header_row=6`; rationale mentions the title block |
| Two-row header (merged "Values" over Building/Contents/BI) | `header_rows_combined=[r-1, r]`; columns like `"Values - Building"` |
| First data row is text-heavy (address, city, occupancy) | Header is still the real header, not the first data row |
| Workbook: "Cover" (notes), "Locations" (data), hidden "Lookup" (codes), empty sheet | Locations = Primary; Lookup = Secondary or Reject; Cover and empty = Reject; nothing crashes |
| Data rows plus "Grand Total" row | Row flagged `total_row`; **not** removed from `frames` |
| Merged cells in data region | Not filled; listed in `data_merged_ranges` |
| Duplicate and blank headers | `__2` suffix and `Unnamed_Col_X`, both logged |
| Zip `02134` stored as text | Value unchanged (leading zero kept) |
| CSV with a 2-line preamble, cp1252, `;` delimiter | Loads; header detected on line 3 |
| Formatted to row 1,048,576 but 50 real rows | Loads quickly; `n_rows == 50` |
| 5,000 rows × 40 columns | Ingestion + profiling < 5 s |
| 0-byte file, `.txt`, corrupt `.xlsx`, `.xls` renamed to `.xlsx` | Correct `IngestionError` code; no crash |
| LLM mocked to return invalid JSON / unknown sheet name / no Primary | Fallback / drop / promote paths behave as specified |

Mock the LLM in tests. No network calls in the test suite.

---

# 7. Acceptance Criteria for This Phase
- `run_ingestion(path)` then `run_sheet_discovery(state)` return a `PipelineState` with both stages validated for every fixture that has data.
- No cell value in `frames` differs from the source cell, apart from header-region merge filling.
- Every sheet appears in the manifest with a classification, a confidence and at least one reasoning string.
- Every structural decision appears in `decisions_log` with a rationale.
- No API key in code. `.env.example` lists the required variables.
- Malformed files produce a friendly message and never a stack trace in the UI.

# Out of Scope (later phases)
Column mapping (Agent 2), type casting and anomaly detection (Agent 3), applying any change to data or removing flagged rows (Agent 4, after human approval), export of `Cleaned_SOV.xlsx` / `Audit_Log`.
