# System Context
You are an expert Python developer and AI engineer building the first phase of an "Agentic SOV Cleansing & Intelligence System" for a Property and Casualty insurance hackathon[cite: 2]. 

A Statement of Values (SOV) is a complex Excel/CSV file containing insured asset data[cite: 2]. Clients submit these with wildly inconsistent formats, multiple tabs, floating headers, and merged cells[cite: 2, 3]. 

Your task is to write production-ready Python code implementing **FR-1 (File Ingestion)**[cite: 5] and **Agent 1 (Sheet Intelligence and Discovery)**[cite: 8].

---

# Architectural Mandate: Hybrid Heuristic-LLM
To process files up to 5,000 rows without timeouts or memory failures (a hard requirement)[cite: 5], you must implement a **Hybrid Heuristic-LLM Architecture**:
1. **Deterministic Python (Pandas)**: Handles structural chaos, file loading, ghost sheet removal, and the "Floating Header" discovery mathematically. 
2. **LLM (Agent 1)**: Receives a lightweight, heavily filtered JSON summary from Pandas (metadata + a 5-row sample) to classify the sheet and determine business value. Do NOT pass full dataframes to the LLM.

---

# Requirements to Implement

## 1. FR-1: Safe Ingestion & Pre-Filtering Engine[cite: 5]
Write a robust Pandas preprocessing pipeline that performs the following:
* **File Support:** Accept `.xlsx` and `.csv` files[cite: 5]. Wrap ingestion in `try/except` blocks to return user-readable error messages for unsupported or corrupted files (NFR-5).
* **Ghost Sheet Removal:** For Excel workbooks, drop any sheet with 0 rows or 0 columns before processing.
* **The "Density Spike" Header Algorithm:** Handle header rows at any position (not necessarily row 1)[cite: 5]. Do this algorithmically:
    * Iterate through the first 30 rows. Calculate the "non-null horizontal string span" (count of distinct columns containing text).
    * Detect the spike: A true header usually transitions from 0-2 strings to a high number of strings.
    * Validate: Ensure the row below the suspected header contains a mix of data types (integers, floats, dates).
    * Clean: Slice the dataframe so the detected header is index 0. Drop all garbage rows above it. Produce a clean internal pandas DataFrame[cite: 5].

## 2. Agent 1: Sheet Intelligence and Discovery Agent[cite: 8]
Once Pandas cleans the structure, Agent 1 evaluates the business relevance of each sheet[cite: 8].
* **Input to Agent 1:** Write a function that extracts a lightweight JSON payload for the LLM containing: `sheet_name`, `detected_header_list`, `total_row_count`, `null_value_percentage`, and a `3-to-5_row_data_sample`[cite: 8].
* **LLM Task:** Analyze structural characteristics, column richness, null-value distribution, and row continuity to score and rank each worksheet[cite: 8].
* **Classification:** The LLM must categorize each sheet as **Primary** (core business data), **Secondary** (reference data), or **Reject** (metadata, summaries)[cite: 8].
* **Output Specification:** The LLM must output a strictly structured JSON response exactly matching this format[cite: 8]:
  ```json
  {
    "sheet_name": "SOV_Data",
    "classification": "Primary",
    "confidence": 0.94,
    "reasoning": [
      "Consistent tabular structure",
      "High header confidence",
      "Low null ratio"
    ]
  }