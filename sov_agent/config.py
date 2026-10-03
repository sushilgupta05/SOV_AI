"""All tunable thresholds and limits in one place."""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- File limits -----------------------------------------------------------
SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".csv"}
MAX_FILE_MB = 25
MAX_ROWS = 50_000
EMPTY_ROW_STOP = 500  # stop scanning after this many consecutive empty rows

# --- Header detection ------------------------------------------------------
HEADER_SCAN_ROWS = 30
HEADER_CONTRAST_WINDOW = 10
MIN_HEADER_SCORE = 0.40
MIN_HEADER_TEXT_RATIO = 0.60  # a header row is mostly labels; numeric-heavy rows are data
HEADER_TIE_EPSILON = 0.02
HEADER_WEIGHTS = {
    "keyword": 0.30,
    "text_ratio": 0.20,
    "contrast": 0.20,
    "coverage": 0.15,
    "uniqueness": 0.15,
}

# --- Sheet discovery -------------------------------------------------------
SHEET_SCORE_WEIGHTS = {
    "target_fields": 0.35,
    "completeness": 0.20,
    "header_confidence": 0.15,
    "row_volume": 0.15,
    "row_continuity": 0.10,
    "name_hint": 0.05,
}
HIDDEN_SHEET_PENALTY = 0.10
REJECT_SCORE = 0.35
PRIMARY_MIN_SCORE = 0.55
SAME_LAYOUT_HEADER_JACCARD = 0.70
DISAGREE_LOW_SCORE = 0.35
DISAGREE_HIGH_SCORE = 0.70
MAX_SHEETS_TO_LLM = 20
SAMPLE_ROWS = 5
SAMPLE_VALUE_MAX_CHARS = 40

# --- LLM ------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))
LLM_ENABLED = _env_bool("LLM_ENABLED", True)
MASK_SAMPLES = _env_bool("MASK_SAMPLES", True)
