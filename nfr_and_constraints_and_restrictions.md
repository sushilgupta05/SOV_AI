# SYSTEM MANDATE: Non-Functional Requirements, Constraints, and Restrictions

> **CRITICAL INSTRUCTION FOR AI:**
> This document lists the absolute boundaries, hard constraints and non-functional requirements (NFRs) for the **Agentic SOV Cleansing & Intelligence System**. Follow them in every line of code, every architectural decision and every agent prompt, in **every phase** of the project. Violating C-01 or C-02 disqualifies the submission. Violating any other rule here costs evaluation points.
>
> Source: *Agentic_SOV_Cleansing_and_Intelligence_System-1.pdf* (Adrosonic Build problem statement). Items marked **[Project rule]** are not in the PDF; they are decisions this team adopted to satisfy the PDF consistently. Section 10 lists ambiguities in the PDF and how we resolve them.

---

## 1. Mandatory Hard Constraints (The "Never Do" List)

| ID | Constraint | Rule |
|---|---|---|
| **C-01** | No auto-transformation | Transformations must **never** be applied without explicit human approval. Silent data mutation is a **disqualifying failure**. |
| **C-02** | No hallucinated data | Never invent, interpolate or fill in missing values with fabricated data. Missing fields must stay **blank** in the output. |
| **C-03** | Schema conformance | Output must contain **exactly** the 17 required fields (Section 3). No additional columns, no renamed fields, no reordered columns. |
| **C-04** | Multi-agent architecture | Single-agent or monolithic pipelines do not score on Agentic Design. State must be shared across **at least 4 distinct agents** (Section 5). |
| **C-05** | Explanation for every decision | Every mapping and every data transformation must carry a human-readable rationale string. Unexplained changes score **zero** on Explainability. |
| **C-06** | Test on provided samples | Evaluation uses the 3 anonymised sample SOV files. The system must process **all 3 without crashing**. |
| **C-07** | Output file naming | Cleaned output must be named exactly **`Cleaned_SOV.xlsx`**. The audit log must be named **`Audit_Log.xlsx`** or **`Audit_Log.json`**. |

**How C-01 and C-02 apply before the human-approval step [Project rule]:**
- Ingestion and analysis (Agents 1–3) may **parse structure** (find the header row, read cells, profile data) but must never change a cell value.
- Anything that would alter or remove data, such as dropping a totals row, casting a type or fixing a value, is only **flagged or recommended** before approval. It is applied by Agent 4 only after a human approves it.
- Merged cells in the **header** may be unpacked so every column has a label. Merged cells in the **data** region are never forward-filled (C-02); they become a recommendation.
- The untouched original data is always kept alongside the working copy.

---

## 2. Non-Functional Requirements (NFRs)

| ID | Requirement | Acceptance criterion |
|---|---|---|
| **NFR-1** | Mapping accuracy | Header mapping accuracy **≥ 74%** on the provided test SOV samples (manual baseline is 53%). |
| **NFR-2** | Anomaly recall | The Data Quality agent must detect **≥ 90%** of known anomalies in the test files (nulls, type errors, logical violations). |
| **NFR-3** | Audit completeness | **100%** of applied transformations appear in the audit log. Each entry records: `source_column`, `target_column`, `transformation_applied`, `before_value`, `after_value`, `confidence`, `approved_by`, `timestamp`. |
| **NFR-4** | Error handling | The system must not crash on malformed input and must return a **user-readable error message** for any unsupported file. No stack traces in the UI. |
| **NFR-5** | Output schema conformance | Cleaned output must pass schema validation: all 17 required columns present, correct data types, no merged cells. |
| **NFR-6** | Explainability | Every agent decision has a human-readable rationale string. No silent transformations. |
| **NFR-7** | Agent state sharing | Agents share context through a **structured state object**. No agent may operate on inputs the previous agent has not validated. |

---

## 3. Target Schema: Data Dictionary (exact, case-sensitive, in this order)

Downstream exposure models perform strict schema validation and **reject** files that deviate from this specification. Column **order is fixed** as listed (C-03).

| # | Field name (exact) | Type | Definition / allowed values |
|---|---|---|---|
| 1 | `Reference` | String | Unique identifier for the property used in insurance documentation. |
| 2 | `Address` | String | Full street address of the insured property. |
| 3 | `City` | String | City where the property is located. |
| 4 | `State` | String | State or region (2-letter abbreviation preferred). |
| 5 | `Zip` | Integer | Postal / ZIP code. |
| 6 | `County` | String | County for regional risk analysis. |
| 7 | `Country` | String | Country; defines jurisdiction for policy coverage. |
| 8 | `Building Value` | Float | Insured value of the building structure (replacement cost / market value). |
| 9 | `Contents` | Float | Value of personal property / contents within the building. |
| 10 | `BI` | Float | Business Income coverage; potential income loss during interruption. |
| 11 | `Occupancy` | String | Use or purpose of the building (residential, commercial, industrial). |
| 12 | `Construction` | String | Construction type or material (wood, steel, concrete, masonry). |
| 13 | `Storeys` | Integer | Number of floors in the building. |
| 14 | `Number of Buildings` | Integer | Total number of buildings at the location. |
| 15 | `Year Built` | Integer | Year the building was constructed. |
| 16 | `Fire Sprinklers (Y/N)` | String | **Only** `Y`, `N`, `Y13` or `Y(13R)`. |
| 17 | `Other` | Float | Additional insured values not classified under Building Value or Contents. |

---

## 4. Strict Output Formatting Rules

**`Cleaned_SOV.xlsx`**
- **Sheet:** a single sheet named exactly `Cleaned_SOV`.
- **Header position:** headers exactly in **row 1**, with the 17 field names from Section 3 in that order.
- **Data position:** data starts at **row 2**.
- **Formatting:** **no merged cells**, **no colour formatting**.
- **Encoding:** UTF-8.
- **Nulls:** missing values are **blank cells**. Never use `0`, `"N/A"`, `"null"`, `"-"` or any other placeholder.
- **Types:** values cast per Section 3 (Float / Integer / String).

**Audit log**
- **File name:** a separate file named `Audit_Log.xlsx` or `Audit_Log.json`, so that `Cleaned_SOV.xlsx` stays single-sheet (see Section 10).
- **Coverage:** one entry per applied transformation, with all 8 fields listed in NFR-3.

**Processing summary report:** Agent 4 must also produce one.

---

## 5. Mandatory Agent Architecture

There are four distinct agents. Each owns one stage of the pipeline, passes structured output to the next, and writes to one shared state object that the human reviewer can see.

| Agent | Must do | Must not do |
|---|---|---|
| **Agent 1: Sheet Intelligence & Discovery** | Scan **all** worksheets. Analyse header quality and density, data consistency, column richness, null distribution, row volume and continuity. Score and rank every sheet, classify it as `Primary` / `Secondary` / `Reject`, and produce a ranked manifest with confidence and reasoning. | Silently drop sheets. |
| **Agent 2: Schema Mapping** | Receive **only Primary sheet(s)** from Agent 1. Run a two-pass mapping to the 17 fields. Give every mapping a confidence (0.0–1.0) and a method. Identify ambiguous or unmapped columns. | Map a column without a confidence, method and rationale. |
| **Agent 3: Data Quality & Reasoning** | Validate mapped fields. Detect missing values, invalid types, duplicates, format inconsistencies, business-rule violations and cross-field anomalies. Use LLM reasoning to explain issues, assess impact, recommend fixes and prioritise them. Build the human-review queue. | Apply any fix. |
| **Agent 4: Controlled Transformation** | Activate **only after explicit human approval**. Apply only approved changes while preserving the original source data. Produce the audit trail, `Cleaned_SOV.xlsx` and a processing summary. | Make any modification that was not approved. |

**Implementation rules [Project rule]:**
- All agents read and write one `PipelineState`.
- Each stage marks itself validated, and the next stage calls `require_validated(...)` before it runs (NFR-7).
- Every decision is appended to `state.decisions_log` with a rationale (NFR-6).

---

## 6. Mandatory Functional Thresholds and Rules

### Ingestion (FR-1)
- Accept `.xlsx` and `.csv` uploads through a **web or desktop UI**.
- Support multi-sheet workbooks. Identify the relevant tabs and the header row **automatically**, wherever the header sits.
- Handle files up to **5,000 rows** without timeout or memory failure.
- Produce a structured internal representation (pandas DataFrame) before passing data to the agents.

### Mapping (FR-2)
- **Pass 1:** exact string match, then normalised fuzzy match (RapidFuzz or equivalent). The fuzzy threshold is **≥ 0.75**.
- **Pass 2:** semantic similarity using sentence embeddings (Sentence-BERT or OpenAI Embeddings), with LLM reasoning, for headers still unresolved.
- Every mapping carries a **confidence (0.0–1.0)** and a **resolution method** (`exact` / `fuzzy` / `semantic` / `none`).
- Columns with confidence **< 0.50** are flagged `human_review_required`.
- Output follows the JSON mapping format in Section 08 of the PDF: `sheet_identified`, `header_row`, `mappings{target, confidence, method, flag}`, `unresolved_count`, `overall_confidence`.

### Data quality (FR-3)
- Per-field completeness rate (% non-null) for **all** mapped columns.
- Type validation on **every row**:
  - `Building Value`, `Contents`, `BI`, `Other` must be numeric.
  - `Zip`, `Storeys`, `Number of Buildings`, `Year Built` must be integers.
  - `Fire Sprinklers (Y/N)` must be one of `Y`, `N`, `Y13`, `Y(13R)`.
- Logical errors:
  - negative monetary values
  - `Year Built` after the **current year** (computed at runtime, never hardcoded)
  - `Storeys` < 1
- Format inconsistencies: currency symbols in numeric fields, mixed date formats, inconsistent state abbreviations.
- Output: a structured quality report with **per-row and per-field** anomaly flags.

### Recommendations (FR-4)
- Every recommendation includes:
  - an **action type**: `column_mapping` | `data_correction` | `standardisation` | `flag_for_review`
  - the proposed transformation with a **before and after example**
  - a plain-English **rationale**
  - a **confidence** score
  - an explicit **uncertainty statement** when confidence is low
- Powered by GPT-4o or an equivalent LLM.
- **Never auto-applied.** Every suggestion goes into the review queue.
- **Re-reasoning:** when a human rejects a recommendation with a note, the agent re-evaluates it, or escalates to the human.

### Human approval (FR-5)
- For each recommendation, show the raw column, proposed mapping, confidence and rationale.
- Let the reviewer **accept, reject or edit** each recommendation individually.
- Show a side-by-side **before / after preview** for at least a sample of rows.
- Offer **"Approve All" only for recommendations with confidence ≥ 0.90**.
- **Block export** until every flagged, low-confidence item has been reviewed.
- A **non-technical reviewer** must be able to understand and act on every recommendation. A working interface matters more than looks.

### Transformation (FR-6)
- Apply **only approved** transformations.
- Rename columns to the exact target names (case-sensitive).
- Cast types per the data dictionary.
- Keep nulls as **empty cells**: no zeros, no placeholders.
- Export and audit as described in Section 4.

---

## 7. Security & Data Privacy Constraints

- Design the system to handle **sensitive commercial data securely**. Evaluators will note data-handling practices.
- Prefer secure data handling over open-source solutions where data security is a concern. For example, Azure OpenAI is the enterprise-grade option.
- **Never hardcode API keys.** Use environment variables (`.env`, which is git-ignored, plus `.env.example`) or a secrets manager.
- **[Project rule]** Send the LLM only compact summaries and samples, never full DataFrames.
- **[Project rule]** Mask PII-like values (names, addresses, emails, phone numbers) in samples sent to the LLM.
- **[Project rule]** Never write raw cell data to application logs.
- The test files are anonymised: insureds, addresses and contacts are synthetic; policy numbers look like `SAMPLE-UMR-XXXX`. Treat them as if they were real.

---

## 8. Performance Expectations

- **Volume:** files up to **5,000 rows** with no timeout or memory failure (hard requirement, FR-1). Larger enterprise files ("thousands of rows, dozens of sheets") must degrade gracefully, not crash.
- **Latency (target, not a hard NFR):** a sub-minute agentic pipeline plus a brief human review. The PDF's impact goal is "< 60 sec agentic pipeline plus human review".
- **LLM calls:** batch where possible (e.g. one call for all sheets in Agent 1) and always have a timeout plus a **deterministic fallback**, so an LLM failure never stops the pipeline (C-06, NFR-4).

---

## 9. Evaluation Targets and Demo Requirements

| Criterion | Weight | Target |
|---|---|---|
| Mapping accuracy | 20% | ≥ 74% of headers correctly mapped across all 3 samples |
| Data quality detection | 15% | ≥ 90% recall on planted anomalies |
| Transformation correctness | 15% | ≥ 95% of output rows match ground truth (type, value, field name) |
| Agentic design quality | 20% | 4 clearly distinct agents, shared state, clear orchestration |
| Explainability | 15% | 100% of recommendations have a clear, accurate rationale |
| Human-in-the-loop UX | 10% | A non-technical reviewer can understand and act on everything |
| Innovation / bonus | 5% | Vector memory, workflow visualisation, multi-format support, iterative re-reasoning, aggregate quality score |

The **demo** has a 10-minute limit and must show all 7 stages on at least one test file:
1. **Upload and sheet detection:** the data sheet and header row are identified.
2. **Mapping JSON with confidences,** including at least one **semantic** match, e.g. "Bldg Repl Cost" → "Building Value".
3. **Quality report:** per-field completeness and at least 3 flagged issues with their types.
4. **Recommendation queue** with rationale for at least one column mapping, one data correction and one standardisation.
5. **Approval workflow:** accept one item, reject one with feedback, and show the agent re-reasoning or escalating.
6. **Transformation applied,** with cleaned output and audit log shown together.
7. **Download `Cleaned_SOV.xlsx`** and show conformance: headers, types, no merged cells.

The 3 test samples are:
1. clean, well-structured headers (baseline)
2. ambiguous or abbreviated headers (semantic test)
3. a multi-sheet layout with missing columns (sheet detection and null-handling test)

---

## 10. Ambiguities in the Problem Statement and Our Resolution [Project rule]

| # | Ambiguity | Resolution |
|---|---|---|
| 1 | FR-6 says "single-sheet .xlsx" but also allows the audit log "as a second sheet". C-07 requires the audit log to be named `Audit_Log.xlsx` / `.json`. | Write the audit log as a **separate** `Audit_Log.xlsx` and/or `Audit_Log.json`. `Cleaned_SOV.xlsx` stays single-sheet. This satisfies every clause. |
| 2 | The mapping example uses `"Fire Sprinklers"`; the data dictionary uses `Fire Sprinklers (Y/N)`. | The output column is always **`Fire Sprinklers (Y/N)`**. The data dictionary wins. |
| 3 | `Zip` is Integer, but US zips can have leading zeros (`02134` → `2134`). | Follow the spec (Integer). Raise a `standardisation` recommendation that states the leading-zero loss so the reviewer approves it knowingly. |
| 4 | FR-2 Pass 2 says "sentence embeddings"; Agent 2 says "LLM reasoning". | Do both: embeddings rank the candidates, and the LLM confirms the choice and writes the rationale. |
| 5 | FR-3 and FR-4 are separate requirements, but the architecture has one "Agent 3". | Agent 3 covers both: quality detection plus the recommendation queue. FR-5 is the UI / orchestration layer, not a fifth agent. |
| 6 | The UTF-8 requirement for `.xlsx`. | `.xlsx` is UTF-8 internally. Also write `Audit_Log.json` and any CSV as UTF-8. |
| 7 | Target fields with no source column (sample 3 has missing columns). | Keep the column in the output, with every cell blank (C-02 + C-03), and flag it in the quality report. |

---

## 11. Compliance Checklist (run before merging any phase)

- [ ] No code path changes data without a recorded human approval (C-01).
- [ ] No value is ever filled, guessed or interpolated; nulls stay blank (C-02).
- [ ] The output has exactly 17 columns, with exact names, in exact order (C-03).
- [ ] The 4 agents are separate modules that communicate only through `PipelineState` (C-04, NFR-7).
- [ ] Every mapping, issue, recommendation and transformation has a rationale string (C-05, NFR-6).
- [ ] All 3 sample files run end-to-end without crashing (C-06).
- [ ] The output files are named `Cleaned_SOV.xlsx` and `Audit_Log.xlsx` / `Audit_Log.json`; the sheet is named `Cleaned_SOV` (C-07).
- [ ] Every applied change has a complete 8-field audit entry (NFR-3).
- [ ] Malformed or unsupported files show a friendly message, never a stack trace (NFR-4).
- [ ] No API keys in code; secrets come from env vars; no raw data in logs.
- [ ] LLM failures fall back to deterministic logic instead of crashing.
