"""Cell-level helpers shared by header detection and profiling."""
from __future__ import annotations

import re
from datetime import date, datetime, time

_NUMERIC_STRIP = re.compile(r"[\s$€£,%()]")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_DATE_PATTERNS = (
    re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?"),
    re.compile(r"^\d{1,2}[- ][A-Za-z]{3,9}[- ]\d{2,4}$"),
    re.compile(r"^[A-Za-z]{3,9} \d{1,2},? \d{4}$"),
)
_BOOL_LITERALS = {"true", "false"}


def is_empty(v: object) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def is_numeric_like(v: object) -> bool:
    """True for numbers and number-looking strings such as '$1,200', '(500)', '45%'."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return not (isinstance(v, float) and v != v)
    if isinstance(v, str):
        s = _NUMERIC_STRIP.sub("", v)
        return bool(s) and bool(_NUMBER.match(s))
    return False


def is_date_like(v: object) -> bool:
    if isinstance(v, (datetime, date, time)):
        return True
    if isinstance(v, str):
        s = v.strip()
        return any(p.match(s) for p in _DATE_PATTERNS)
    return False


def is_text(v: object) -> bool:
    """Non-empty string that is not a number, date or boolean literal.

    CSV cells are always strings, so this must be used instead of isinstance(v, str).
    """
    if not isinstance(v, str) or not v.strip():
        return False
    s = v.strip()
    if s.lower() in _BOOL_LITERALS:
        return False
    return not (is_numeric_like(s) or is_date_like(s))


def clean_header_text(v: object) -> str:
    """Strip and collapse whitespace/newlines; no case changes."""
    if is_empty(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def col_letter(idx0: int) -> str:
    """0-based column index -> Excel column letters."""
    n, letters = idx0 + 1, ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
