"""CSV parsing service for the charging session import flow.

Provides utilities for parsing uploaded CSV files, extracting column headers,
auto-detecting column-to-database-field mappings, and preparing data for import.
"""

import csv
import hashlib
import io
import uuid
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# CSV field options — descriptors for all mappable EVChargingSession columns
# ---------------------------------------------------------------------------

DB_FIELD_OPTIONS = [
    {
        "field": "session_start_utc",
        "label": "Session Start (UTC)",
        "description": "Timestamp of when the charge session began",
        "required": False,
        "important": True,
    },
    {
        "field": "session_end_utc",
        "label": "Session End (UTC)",
        "description": "Timestamp of when the charge session ended",
        "required": False,
        "important": False,
    },
    {
        "field": "energy_kwh",
        "label": "Energy (kWh)",
        "description": "Total energy delivered during the session",
        "required": False,
        "important": True,
    },
    {
        "field": "location_name",
        "label": "Location Name",
        "description": "Name or label for the charging location (e.g., Home, Work)",
        "required": False,
        "important": False,
    },
    {
        "field": "charge_type",
        "label": "Charge Type",
        "description": "AC or DC charger type",
        "required": False,
        "important": False,
    },
    {
        "field": "location_type",
        "label": "Location Type",
        "description": "Category of location: home, work, or public",
        "required": False,
        "important": False,
    },
    {
        "field": "is_free",
        "label": "Is Free",
        "description": "Whether the session was at a free charging location",
        "required": False,
        "important": False,
    },
    {
        "field": "charge_duration_seconds",
        "label": "Charge Duration (seconds)",
        "description": "Duration of the charge in seconds (use duration_minutes field for minutes)",
        "required": False,
        "important": False,
    },
    {
        "field": "charging_kw",
        "label": "Average Power (kW)",
        "description": "Average charging power in kilowatts",
        "required": False,
        "important": False,
    },
    {
        "field": "max_power",
        "label": "Max Power (kW)",
        "description": "Peak charging power in kilowatts",
        "required": False,
        "important": False,
    },
    {
        "field": "min_power",
        "label": "Min Power (kW)",
        "description": "Minimum charging power in kilowatts",
        "required": False,
        "important": False,
    },
    {
        "field": "start_soc",
        "label": "Start SOC (%)",
        "description": "Battery state of charge at session start (percentage)",
        "required": False,
        "important": False,
    },
    {
        "field": "end_soc",
        "label": "End SOC (%)",
        "description": "Battery state of charge at session end (percentage)",
        "required": False,
        "important": False,
    },
    {
        "field": "cost",
        "label": "Cost ($)",
        "description": "Total session cost in dollars",
        "required": False,
        "important": False,
    },
    {
        "field": "cost_without_overrides",
        "label": "Cost Without Overrides ($)",
        "description": "Session cost before any manual price overrides",
        "required": False,
        "important": False,
    },
    {
        "field": "miles_added",
        "label": "Miles Added",
        "description": "Estimated range added during the session",
        "required": False,
        "important": False,
    },
    {
        "field": "charging_voltage",
        "label": "Charging Voltage (V)",
        "description": "Charging voltage in volts",
        "required": False,
        "important": False,
    },
    {
        "field": "charging_amperage",
        "label": "Charging Amperage (A)",
        "description": "Charging current in amps",
        "required": False,
        "important": False,
    },
    {
        "field": "is_complete",
        "label": "Is Complete",
        "description": "Whether the session completed normally",
        "required": False,
        "important": False,
    },
    {
        "field": "session_id",
        "label": "Session ID (UUID)",
        "description": "Unique identifier for the session — auto-generated if not provided",
        "required": False,
        "important": False,
    },
    {
        "field": "device_id",
        "label": "Device ID (VIN)",
        "description": "Vehicle identifier (VIN or device ID)",
        "required": False,
        "important": False,
    },
    {
        "field": "recorded_at",
        "label": "Recorded At",
        "description": "Timestamp when the session data was recorded by the source system",
        "required": False,
        "important": False,
    },
    {
        "field": "source_system",
        "label": "Source System",
        "description": "Origin of the data (e.g., ha_fordpass, csv_import)",
        "required": False,
        "important": False,
    },
]

# ---------------------------------------------------------------------------
# Seed-format COLUMN_MAP for primary auto-detect lookup
# Maps CSV header names -> DB field names (without transform functions)
# ---------------------------------------------------------------------------

_SEED_COLUMN_MAP: dict[str, str] = {
    "session_id": "session_id",
    "location_name": "location_name",
    "start_time": "session_start_utc",
    "end_time": "session_end_utc",
    "duration_minutes": "charge_duration_seconds",
    "energy_consumed_kwh": "energy_kwh",
    "average_power_kw": "charging_kw",
    "max_power": "max_power",
    "min_power": "min_power",
    "start_soc_percent": "start_soc",
    "end_soc_percent": "end_soc",
    "cost_total": "cost",
    "cost_without_overrides": "cost_without_overrides",
    "miles_added": "miles_added",
    "charging_voltage": "charging_voltage",
    "charging_amperage": "charging_amperage",
    "is_complete": "is_complete",
    "recorded_at": "recorded_at",
    # Additional common aliases
    "charge_type": "charge_type",
    "charger_type": "charge_type",
    "location_type": "location_type",
    "is_free": "is_free",
    "device_id": "device_id",
    "vin": "device_id",
    "source_system": "source_system",
    "energy_kwh": "energy_kwh",
    "session_start": "session_start_utc",
    "session_end": "session_end_utc",
}


def _normalize_key(s: str) -> str:
    """Normalize a string for fuzzy matching: lowercase, strip separators."""
    return s.lower().replace("_", "").replace("-", "").replace(" ", "")


# Build normalized seed map once at module load
_NORM_SEED_MAP: dict[str, str] = {_normalize_key(k): v for k, v in _SEED_COLUMN_MAP.items()}

# Build normalized DB field map: normalized_field_name -> field_name
_NORM_DB_FIELDS: dict[str, str] = {
    _normalize_key(opt["field"]): opt["field"] for opt in DB_FIELD_OPTIONS  # type: ignore[arg-type]
}

# Keyword hints for fuzzy fallback: if normalized header contains these tokens,
# map to the associated DB field
_KEYWORD_HINTS: list[tuple[list[str], str]] = [
    (["start", "time"], "session_start_utc"),
    (["start", "utc"], "session_start_utc"),
    (["end", "time"], "session_end_utc"),
    (["end", "utc"], "session_end_utc"),
    (["energy", "kwh"], "energy_kwh"),
    (["energy", "consumed"], "energy_kwh"),
    (["kwh"], "energy_kwh"),
    (["duration", "min"], "charge_duration_seconds"),
    (["avg", "power"], "charging_kw"),
    (["average", "power"], "charging_kw"),
    (["max", "power"], "max_power"),
    (["min", "power"], "min_power"),
    (["start", "soc"], "start_soc"),
    (["end", "soc"], "end_soc"),
    (["soc", "start"], "start_soc"),
    (["soc", "end"], "end_soc"),
    (["cost", "total"], "cost"),
    (["miles"], "miles_added"),
    (["voltage"], "charging_voltage"),
    (["amperage"], "charging_amperage"),
    (["location", "name"], "location_name"),
    (["location", "type"], "location_type"),
    (["charge", "type"], "charge_type"),
    (["charger", "type"], "charge_type"),
    (["is", "free"], "is_free"),
    (["is", "complete"], "is_complete"),
    (["recorded"], "recorded_at"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_csv_file(file_contents: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Parse CSV bytes and return (headers, rows).

    Handles UTF-8 BOM. Returns headers as a list of strings and rows as a
    list of raw string dicts (values not transformed).

    Raises ValueError if the file cannot be decoded or has no headers.
    """
    # Decode bytes — handle UTF-8 BOM
    try:
        text = file_contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = file_contents.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Cannot decode CSV file: {exc}") from exc

    if not text.strip():
        raise ValueError("CSV file is empty")

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames
    if not headers:
        raise ValueError("CSV file has no headers")

    rows = list(reader)
    return list(headers), rows


def get_db_field_options() -> list[dict]:
    """Return list of mappable EVChargingSession column descriptors."""
    return list(DB_FIELD_OPTIONS)


def auto_detect_mappings(
    csv_headers: list[str],
    db_fields: list[dict],
) -> dict[str, str]:
    """Map CSV headers to DB field names using name similarity.

    Strategy:
    1. Exact match against seed COLUMN_MAP keys (highest confidence)
    2. Exact match against normalized DB field names
    3. Keyword-hint multi-token matching
    4. Single-token substring containment fallback

    Returns dict of {csv_header: db_field_name}.
    Only maps headers that have a confident match — no guess-and-be-wrong.
    """
    valid_fields = {opt["field"] for opt in db_fields}
    mappings: dict[str, str] = {}

    for header in csv_headers:
        norm = _normalize_key(header)

        # 1. Exact match in seed COLUMN_MAP (direct CSV column name lookup)
        if header in _SEED_COLUMN_MAP:
            db_field = _SEED_COLUMN_MAP[header]
            if db_field in valid_fields:
                mappings[header] = db_field
                continue

        # 2. Normalized exact match against seed map keys
        if norm in _NORM_SEED_MAP:
            db_field = _NORM_SEED_MAP[norm]
            if db_field in valid_fields:
                mappings[header] = db_field
                continue

        # 3. Normalized exact match against DB field names
        if norm in _NORM_DB_FIELDS:
            db_field = _NORM_DB_FIELDS[norm]
            if db_field in valid_fields:
                mappings[header] = db_field
                continue

        # 4. Keyword-hint multi-token matching
        matched = False
        for tokens, db_field in _KEYWORD_HINTS:
            if all(t in norm for t in tokens) and db_field in valid_fields:
                mappings[header] = db_field
                matched = True
                break
        if matched:
            continue

        # 5. Single-token substring: if one word in the header appears in a
        #    DB field name — only when unambiguous (exactly one candidate)
        candidates = []
        for opt in db_fields:
            field_norm = _normalize_key(opt["field"])  # type: ignore[arg-type]
            if norm in field_norm or field_norm in norm:
                candidates.append(opt["field"])
        if len(candidates) == 1:
            mappings[header] = candidates[0]

    return mappings


def make_session_id(
    start_time: Optional[datetime],
    location_name: Optional[str],
    energy_kwh: Optional[float],
) -> uuid.UUID:
    """Generate a deterministic UUID from session fields using MD5.

    Copied from scripts/seed.py for consistent duplicate detection.
    """
    start_str = start_time.isoformat() if start_time else ""
    loc_str = location_name or ""
    kwh_str = str(energy_kwh) if energy_kwh is not None else ""
    key = f"{start_str}|{loc_str}|{kwh_str}"
    return uuid.UUID(bytes=hashlib.md5(key.encode()).digest())


def detect_duplicates(rows: list[dict], db_session: object) -> list[dict]:
    """Stub: mark all rows as 'new'. Full implementation in Plan 02.

    Returns rows unchanged with a '_status' field set to 'new'.
    """
    result = []
    for row in rows:
        row_copy = dict(row)
        row_copy["_status"] = "new"
        result.append(row_copy)
    return result
