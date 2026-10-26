"""Collapse the charger-type codes sources send into AC or DC."""

from __future__ import annotations

# Charger type normalization mapping — collapse all variants to AC/DC codes
# matching csv_parser._normalize_charge_type. Level 1/2 granularity is
# intentionally discarded: callers that need it can derive it from the EVSE
# power/voltage columns.
CHARGER_TYPE_MAP = {
    "AC": "AC",
    "AC_BASIC": "AC",
    "AC_LEVEL_1": "AC",
    "AC_LEVEL_2": "AC",
    "AC LEVEL 1": "AC",
    "AC LEVEL 2": "AC",
    "LEVEL_1": "AC",
    "LEVEL_2": "AC",
    "LEVEL 1": "AC",
    "LEVEL 2": "AC",
    "L1": "AC",
    "L2": "AC",
    "DC": "DC",
    "DC_FAST": "DC",
    "DC_DCFAST": "DC",
    "DC_COMBO": "DC",
    "DC FAST": "DC",
    "DCFC": "DC",
    "L3": "DC",
    "LEVEL 3": "DC",
}


def normalize_charge_type(raw: str | None) -> str | None:
    """Normalize charger type string to 'AC' or 'DC'.

    Returns None for empty input. Unrecognized values fall back to the raw
    string (uppercased) so debugging still surfaces the unknown code.
    """
    if not raw:
        return None
    key = raw.strip().upper()
    if not key:
        return None
    return CHARGER_TYPE_MAP.get(key, key)
