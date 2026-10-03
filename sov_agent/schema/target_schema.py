"""The 17-field target schema (Section 08) and the SOV header vocabulary.

Shared by header detection (FR-1), sheet discovery (Agent 1) and schema mapping (Agent 2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class TargetField:
    name: str  # exact, case-sensitive output column name
    dtype: str  # "str" | "int" | "float"
    definition: str
    synonyms: tuple[str, ...]


TARGET_FIELDS: tuple[TargetField, ...] = (
    TargetField("Reference", "str", "Unique identifier for the property.",
                ("reference", "ref", "loc", "loc #", "location no", "location number", "location id",
                 "site id", "property id", "bldg #", "building no", "building number", "item #", "item no")),
    TargetField("Address", "str", "Full street address of the insured property.",
                ("address", "street", "street address", "addr", "address 1", "address line 1",
                 "location address")),
    TargetField("City", "str", "City where the property is located.",
                ("city", "town", "municipality")),
    TargetField("State", "str", "State or region (2-letter abbreviation preferred).",
                ("state", "st", "province", "region", "state code")),
    TargetField("Zip", "int", "Postal / ZIP code.",
                ("zip", "zip code", "zipcode", "postal code", "postcode", "postal")),
    TargetField("County", "str", "County for regional risk analysis.",
                ("county", "parish")),
    TargetField("Country", "str", "Country; defines jurisdiction.",
                ("country", "ctry", "nation", "country code", "country name")),
    TargetField("Building Value", "float", "Insured value of the building structure.",
                ("building value", "bldg value", "building", "bldg", "building limit",
                 "replacement cost", "repl cost", "bldg repl cost", "bldg repl cost new",
                 "building tiv", "building replacement cost")),
    TargetField("Contents", "float", "Value of contents / personal property.",
                ("contents", "cont", "bpp", "business personal property", "personal property",
                 "contents value")),
    TargetField("BI", "float", "Business Income coverage.",
                ("bi", "business income", "business interruption", "bi ee", "bi/ee",
                 "loss of rents", "rental income", "time element", "bi value", "business interruption value")),
    TargetField("Occupancy", "str", "Use or purpose of the building.",
                ("occupancy", "occ", "occupancy type", "use", "property use", "occupancy description")),
    TargetField("Construction", "str", "Construction type or material.",
                ("construction", "const", "constr", "construction type", "iso class",
                 "construction class", "building material")),
    TargetField("Storeys", "int", "Number of floors.",
                ("stories", "storeys", "floors", "# of stories", "no of stories", "no of floors",
                 "num stories", "number of stories", "number of floors", "# floor", "# floors")),
    TargetField("Number of Buildings", "int", "Number of buildings at the location.",
                ("number of buildings", "# bldgs", "# of bldgs", "no of buildings", "num buildings",
                 "building count", "bldg count", "# of buildings")),
    TargetField("Year Built", "int", "Year the building was constructed.",
                ("year built", "yr built", "yr blt", "yob", "year of construction", "built",
                 "year constructed")),
    TargetField("Fire Sprinklers (Y/N)", "str", "Sprinklers installed: Y, N, Y13 or Y(13R).",
                ("fire sprinklers", "sprinklers", "sprinklered", "sprinkler", "spr", "fire prot",
                 "fire protection", "sprinkler y n", "sprink", "% sprink", "% sprinklered", "sprinkler %")),
    TargetField("Other", "float", "Additional insured values.",
                ("other", "other values", "other tiv", "misc", "outdoor property", "equipment")),
)

TARGET_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in TARGET_FIELDS)

# Words that indicate a header row even though they are not target fields.
GENERAL_SOV_TERMS: tuple[str, ...] = (
    "tiv", "total insured value", "total value", "total tiv", "values", "value", "limit", "deductible",
    "flood", "flood zone", "eq", "earthquake", "named storm", "wind", "latitude", "longitude", "lat",
    "long", "policy", "insured", "named insured", "premium", "sq ft", "square footage", "square feet",
    "area", "protection class", "description", "location", "name",
)

_SHORT_SYNONYM_LEN = 3


def normalise(text: object) -> str:
    """Lowercase, '#' -> 'no', '&' -> 'and', other punctuation -> space, collapse whitespace."""
    s = str(text).lower()
    s = s.replace("#", " no ").replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _vocabulary() -> list[tuple[str, str | None]]:
    """(normalised synonym, target field or None for general term), longest first."""
    vocab: list[tuple[str, str | None]] = []
    for field in TARGET_FIELDS:
        for syn in (field.name, *field.synonyms):
            vocab.append((normalise(syn), field.name))
    for term in GENERAL_SOV_TERMS:
        vocab.append((normalise(term), None))
    # de-duplicate, keep first owner, longest synonyms first so the most specific wins
    seen: dict[str, str | None] = {}
    for syn, owner in vocab:
        if syn and syn not in seen:
            seen[syn] = owner
    return sorted(seen.items(), key=lambda kv: len(kv[0]), reverse=True)


@dataclass(frozen=True)
class TermHit:
    target_field: str | None  # None when only a general SOV term matched
    synonym: str


def header_term_hit(cell: object) -> TermHit | None:
    """Return the most specific vocabulary match for a header cell, or None.

    Exact whole-cell matches win; then target-field synonyms beat general SOV terms (so
    'Location - City' resolves to City, not the general term 'location'). Synonyms of <= 3
    characters (e.g. 'st', 'bi', 'occ') only count on an exact match; longer ones count as a
    whole-word substring.
    """
    if cell is None:
        return None
    norm = normalise(cell)
    if not norm or len(norm) > 80:
        return None
    for syn, owner in _vocabulary():
        if norm == syn:
            return TermHit(owner, syn)
    general: TermHit | None = None
    for syn, owner in _vocabulary():
        # (s|es)? lets "building" match "Buildings" and "value" match "Values"
        if len(syn) <= _SHORT_SYNONYM_LEN or not re.search(rf"\b{re.escape(syn)}(s|es)?\b", norm):
            continue
        if owner is not None:
            return TermHit(owner, syn)
        general = general or TermHit(None, syn)
    return general
