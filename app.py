"""Minimal FR-1 UI: upload an SOV, see sheet discovery, header detection and a data preview.

Run:  streamlit run app.py
"""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from sov_agent import config
from sov_agent.errors import IngestionError
from sov_agent.pipeline import run_phase1
from sov_agent.state import PipelineState, override_sheet_selection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

st.set_page_config(page_title="SOV Intelligence", layout="wide")
st.title("Agentic SOV Cleansing & Intelligence System")
st.caption("Phase 1: file ingestion (FR-1) and Agent 1 sheet discovery")

with st.sidebar:
    st.subheader("Settings")
    st.write(f"LLM: `{config.LLM_PROVIDER}` / `{config.LLM_MODEL}`"
             + ("" if config.LLM_ENABLED else " (disabled)"))
    st.write(f"PII masking in LLM samples: {'on' if config.MASK_SAMPLES else 'off'}")
    reviewer = st.text_input("Reviewer name", value="reviewer")

upload = st.file_uploader("Upload an SOV file", type=["xlsx", "xlsm", "csv"])
if upload is None:
    st.info("Upload an .xlsx or .csv Statement of Values to begin.")
    st.stop()

key = f"{upload.name}:{upload.size}"
if st.session_state.get("key") != key:
    st.session_state.pop("state", None)
    st.session_state["key"] = key
    with st.spinner("Reading file and analysing sheets..."):
        try:
            st.session_state["state"] = run_phase1(upload.getvalue(), upload.name)
        except IngestionError as exc:
            logger.warning("Ingestion failed: %s %s", exc.code, exc.detail)
            st.session_state["error"] = exc.user_message
        except Exception:  # NFR-4: never show a stack trace to the user
            logger.exception("Unexpected failure")
            st.session_state["error"] = "Something went wrong while reading this file. Please check it opens in Excel and try again."
        else:
            st.session_state.pop("error", None)

if "error" in st.session_state:
    st.error(st.session_state["error"])
    st.stop()

state: PipelineState = st.session_state["state"]
meta = state.file_meta
st.success(f"Loaded **{meta['name']}**: {meta['sheet_count']} sheet(s), {meta['size_bytes']:,} bytes. "
           f"Selected data sheet(s): **{', '.join(state.selected_sheets) or 'none'}**")
for err in state.errors:
    st.warning(err["message"])

# --- Sheet manifest --------------------------------------------------------
st.header("1. Sheet discovery (Agent 1)")
manifest_df = pd.DataFrame([{
    "Rank": m.rank,
    "Sheet": m.sheet_name,
    "Classification": m.classification,
    "Confidence": round(m.confidence, 2),
    "Structural score": round(m.deterministic_score, 2),
    "Method": "LLM" if m.method == "llm" else "Rule-based fallback",
    "Needs review": "⚠ yes" if m.needs_review else "",
    "Reasoning": "; ".join(m.reasoning),
} for m in state.manifest])
st.dataframe(manifest_df, hide_index=True, width="stretch")

with st.form("override"):
    chosen = st.multiselect("Data sheet(s) to use for mapping", options=list(state.profiles),
                            default=state.selected_sheets)
    if st.form_submit_button("Confirm sheet selection"):
        try:
            override_sheet_selection(state, chosen, reviewer=reviewer or "reviewer")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

# --- Per-sheet structure ---------------------------------------------------
st.header("2. Header detection and structure")
for name, p in state.profiles.items():
    label = f"{name}: header row {p.header_row}" if p.header_row else f"{name}: no table found"
    with st.expander(label, expanded=name in state.selected_sheets):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Data rows", p.n_rows)
        c2.metric("Columns", p.n_cols)
        c3.metric("Empty cells", f"{p.null_pct:.0%}")
        c4.metric("Target fields recognised", f"{len(p.target_fields_hit)} / 17")
        if p.header_rationale:
            st.write("**Why this header row:** " + p.header_rationale)
        if p.hidden:
            st.write("This sheet is hidden in the workbook.")
        for note in p.structural_notes:
            st.write(f"- {note}")
        if p.row_flags:
            st.write("**Flagged rows** (kept; removal will be proposed for review later):")
            st.dataframe(pd.DataFrame([{"Source row": r, "Flags": ", ".join(f)}
                                       for r, f in sorted(p.row_flags.items())]), hide_index=True)

# --- Preview ---------------------------------------------------------------
st.header("3. Data preview")
previewable = [n for n in state.profiles if n in state.frames]
if previewable:
    default = state.selected_sheets[0] if state.selected_sheets and state.selected_sheets[0] in previewable else previewable[0]
    sheet = st.selectbox("Sheet", previewable, index=previewable.index(default))
    preview = state.frames[sheet].head(10).map(lambda v: "" if v is None else str(v))
    st.caption("Index = original row number in the source file. Values are shown exactly as read; nothing is changed yet.")
    st.dataframe(preview, width="stretch")

# --- Decision log ----------------------------------------------------------
st.header("4. Decision log")
with st.expander(f"{len(state.decisions_log)} decisions recorded"):
    st.dataframe(pd.DataFrame([d.model_dump() for d in state.decisions_log]), hide_index=True,
                 width="stretch")
st.download_button("Download pipeline state (JSON)", state.model_dump_json(indent=2),
                   file_name=f"pipeline_state_{state.run_id}.json", mime="application/json")
