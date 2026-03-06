"""CSV parsing service for the charging session import flow.

Provides utilities for parsing uploaded CSV files, extracting column headers,
auto-detecting column-to-database-field mappings, and preparing data for import.
"""

import csv
import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
        "field": "network_id",
        "label": "Charging Network",
        "description": "Network name (e.g. Electrify America, Tesla Supercharger, ChargePoint). Matched to configured networks automatically.",
        "required": False,
        "important": False,
        "csv_header": "charging_network",
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
    "charging_type": "charge_type",
    "location_type": "location_type",
    "network_id": "network_id",
    "charging_network": "network_id",
    "network_name": "network_id",
    "network": "network_id",
    "is_free": "is_free",
    "device_id": "device_id",
    "vin": "device_id",
    "source_system": "source_system",
    "energy_kwh": "energy_kwh",
    "charger_kwh": "energy_kwh",
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
    (["network", "id"], "network_id"),
    (["network", "name"], "network_id"),
    (["charging", "network"], "network_id"),
    (["network"], "network_id"),
    (["charge", "type"], "charge_type"),
    (["charger", "type"], "charge_type"),
    (["charging", "type"], "charge_type"),
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


# ---------------------------------------------------------------------------
# Parsing helpers (adapted from scripts/seed.py)
# ---------------------------------------------------------------------------

_FREE_LOCATIONS = {"Work", "Dealership"}
_WORK_LOCATIONS = {"Work"}
_HOME_LOCATIONS = {"Home"}
_NETWORK_NAMES = {"Tesla", "Supercharger", "Electrify America", "ElectrifyAmerica", "EA", "EVgo", "Charge Point", "ChargePoint"}

def _str_or_none(v: str) -> Optional[str]:
    """Return stripped string or None if empty."""
    v = v.strip() if v else ""
    return v if v else None


def _float_or_none(v: str) -> Optional[float]:
    """Return float or None if empty/invalid."""
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_bool(v: str) -> bool:
    """Return True for 'True'/'1'/'true'/'yes', False otherwise."""
    return v.strip().lower() in ("true", "1", "yes") if v else False


def _parse_timestamp(v: str) -> Optional[datetime]:
    """Parse ISO timestamp string to timezone-aware datetime.

    If result is naive (no tzinfo), treats as UTC.
    """
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_timestamp_with_tz(v: str, import_tz: str = "UTC") -> Optional[datetime]:
    """Parse ISO timestamp string to timezone-aware datetime using the given timezone.

    If the parsed datetime is naive (no tzinfo), treats it as being in ``import_tz``
    and converts to UTC for storage.  If the datetime already carries tzinfo, the
    existing timezone is respected and the value is converted to UTC.
    """
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            # Treat naive timestamp as being in the user-selected import timezone
            tz = ZoneInfo(import_tz) if import_tz and import_tz != "UTC" else timezone.utc
            dt = dt.replace(tzinfo=tz).astimezone(timezone.utc)
        else:
            # Already has timezone — convert to UTC
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError, KeyError):
        return None


def _parse_uuid(v: str) -> Optional[uuid.UUID]:
    """Parse a UUID string, returning None if empty or invalid."""
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return uuid.UUID(v)
    except (ValueError, AttributeError):
        return None


def _normalize_charge_type(charger_type: str, location_name: str) -> Optional[str]:
    """Normalize charger type to 'AC' or 'DC'."""
    ct = charger_type.strip() if charger_type else ""
    if ct in ("AC Level 2", "AC_BASIC", "level_2", "ac_charging", "AC Level 1"):
        return "AC"
    if ct in ("DC_FAST", "dc_fast", "dc_charging"):
        return "DC"
    loc = location_name.strip() if location_name else ""
    if not ct and loc == "Work":
        return "AC"
    return None


def _classify_location_type(location_name: str) -> str:
    """Classify location as 'home', 'work', or 'public'."""
    loc = location_name.strip() if location_name else ""
    if loc in _HOME_LOCATIONS or loc.lower() == "home":
        return "home"
    if loc in _WORK_LOCATIONS:
        return "work"
    return "public"

def _classify_network_name(network_id: str) -> str:
    """Classify network name as most popular options 'Tesla', 'Electrify America'."""
    net = network_id.strip() if network_id else ""
    for names in _NETWORK_NAMES:
        if net.lower() == names.lower():
            return names
    return "Unknown"

def _classify_is_free(location_name: str) -> bool:
    """Return True if the location is a free charging location."""
    loc = location_name.strip() if location_name else ""
    return loc in _FREE_LOCATIONS


# ---------------------------------------------------------------------------
# Per-DB-field parse functions for transform_rows
# ---------------------------------------------------------------------------

# Maps db_field_name -> parse function for each column that needs type conversion
_DB_FIELD_PARSERS: dict[str, object] = {
    "session_start_utc": _parse_timestamp,
    "session_end_utc": _parse_timestamp,
    "recorded_at": _parse_timestamp,
    "energy_kwh": _float_or_none,
    "cost": _float_or_none,
    "cost_without_overrides": _float_or_none,
    "charging_kw": _float_or_none,
    "max_power": _float_or_none,
    "min_power": _float_or_none,
    "start_soc": _float_or_none,
    "end_soc": _float_or_none,
    "miles_added": _float_or_none,
    "charging_voltage": _float_or_none,
    "charging_amperage": _float_or_none,
    "is_complete": _parse_bool,
    "is_free": _parse_bool,
    "location_name": _str_or_none,
    "network_id": _str_or_none,
    "charge_type": _str_or_none,
    "location_type": _str_or_none,
    "device_id": _str_or_none,
    "source_system": _str_or_none,
    "session_id": _parse_uuid,
    # charge_duration_seconds is handled specially (may come as minutes)
}


_TIMESTAMP_FIELDS = {"session_start_utc", "session_end_utc", "recorded_at"}


def transform_rows(
    raw_rows: list[dict],
    column_mapping: dict[str, str],
    import_tz: str = "UTC",
) -> list[dict]:
    """Transform raw CSV rows into DB-ready dicts using the given column mapping.

    Args:
        raw_rows: List of raw string dicts from parse_csv_file.
        column_mapping: Dict mapping CSV header names to DB field names.
                        Empty string values mean "skip this column".
        import_tz: IANA timezone name for interpreting naive timestamps in the CSV.

    Returns:
        List of transformed row dicts with:
        - DB field names and parsed values
        - _row_index: 0-based position in input
        - _status: 'new' | 'error'
        - _error: error message (only on error rows)
        - session_id: deterministic UUID
        - source_system: 'csv_import'
        - is_complete: True
    """
    # Build effective mapping (exclude skip entries)
    effective_mapping = {k: v for k, v in column_mapping.items() if v}

    # Identify if duration_minutes is being mapped to charge_duration_seconds
    # (needs * 60 conversion like seed.py)
    duration_csv_cols = {
        csv_col
        for csv_col, db_col in effective_mapping.items()
        if db_col == "charge_duration_seconds"
        and _normalize_key(csv_col) in (_normalize_key("duration_minutes"),)
    }

    result = []
    for idx, raw_row in enumerate(raw_rows):
        db_row: dict = {}

        for csv_col, db_col in effective_mapping.items():
            raw_val = raw_row.get(csv_col, "")

            # Special case: duration in minutes -> seconds
            if csv_col in duration_csv_cols and db_col == "charge_duration_seconds":
                stripped = raw_val.strip() if raw_val else ""
                db_row[db_col] = float(stripped) * 60 if stripped else None
                continue

            # Use timezone-aware parser for timestamp fields
            if db_col in _TIMESTAMP_FIELDS:
                db_row[db_col] = _parse_timestamp_with_tz(raw_val, import_tz)
                continue

            # Use registered parser or fall through as string
            parser = _DB_FIELD_PARSERS.get(db_col)
            if parser is not None:
                db_row[db_col] = parser(raw_val)  # type: ignore[operator]
            else:
                # Numeric fallback for any unmapped field
                db_row[db_col] = _str_or_none(raw_val)

        # --- Computed / override fields ---
        location_name = db_row.get("location_name") or ""
        charger_type_raw = db_row.get("charge_type") or ""

        # Normalize charge_type (overwrite whatever was parsed from CSV)
        db_row["charge_type"] = _normalize_charge_type(charger_type_raw, location_name)
        db_row["location_type"] = _classify_location_type(location_name)
        db_row["is_free"] = _classify_is_free(location_name)
        db_row["source_system"] = "csv_import"
        db_row["is_complete"] = True

        # Generate deterministic session_id if not provided as a valid UUID
        existing_session_id = db_row.get("session_id")
        if existing_session_id is None:
            db_row["session_id"] = make_session_id(
                db_row.get("session_start_utc"),
                location_name or None,
                db_row.get("energy_kwh"),
            )

        # --- Status ---
        db_row["_row_index"] = idx
        energy = db_row.get("energy_kwh")
        start = db_row.get("session_start_utc")
        if energy is None and start is None:
            db_row["_status"] = "error"
            db_row["_error"] = "Missing energy and start time"
        else:
            db_row["_status"] = "new"

        result.append(db_row)

    return result


async def detect_duplicates(rows: list[dict], db_session: AsyncSession) -> list[dict]:
    """Mark rows as duplicate or fuzzy_duplicate by querying the database.

    Layer 1 (deterministic): Match by session_id.
    Layer 2 (fuzzy): Match by timestamp window (±1 hour), location, and energy (±10%).

    Args:
        rows: List of transformed row dicts from transform_rows.
        db_session: Active SQLAlchemy AsyncSession.

    Returns:
        Updated rows list with _status and optional _matched_id set.
    """
    # Collect session_ids from non-error rows
    session_ids = [
        str(row["session_id"])
        for row in rows
        if row.get("_status") != "error" and row.get("session_id") is not None
    ]

    # Layer 1: deterministic session_id match
    matched_ids: set[str] = set()
    if session_ids:
        result = await db_session.execute(
            text(
                "SELECT session_id FROM ev_charging_session "
                "WHERE session_id = ANY(:ids)"
            ),
            {"ids": session_ids},
        )
        for (sid,) in result.fetchall():
            matched_ids.add(str(sid))

    # Apply Layer 1 matches
    for row in rows:
        if row.get("_status") == "error":
            continue
        sid = str(row.get("session_id", ""))
        if sid in matched_ids:
            row["_status"] = "duplicate"

    # Layer 2: fuzzy matching for remaining 'new' rows that have a start time
    for row in rows:
        if row.get("_status") != "new":
            continue
        start = row.get("session_start_utc")
        if start is None:
            continue
        location = row.get("location_name") or ""
        energy = row.get("energy_kwh")

        window_start = start - timedelta(hours=1)
        window_end = start + timedelta(hours=1)

        fuzzy_result = await db_session.execute(
            text(
                """
                SELECT id, energy_kwh
                FROM ev_charging_session
                WHERE session_start_utc BETWEEN :window_start AND :window_end
                  AND location_name = :location
                """
            ),
            {
                "window_start": window_start,
                "window_end": window_end,
                "location": location,
            },
        )
        fuzzy_matches = fuzzy_result.fetchall()

        for match_id, match_energy in fuzzy_matches:
            # Check energy within 10% tolerance
            if energy is not None and match_energy is not None:
                tolerance = abs(float(match_energy)) * 0.1
                if abs(float(energy) - float(match_energy)) <= tolerance:
                    row["_status"] = "fuzzy_duplicate"
                    row["_matched_id"] = match_id
                    break
            elif energy is None and match_energy is None:
                row["_status"] = "fuzzy_duplicate"
                row["_matched_id"] = match_id
                break

    return rows


# ---------------------------------------------------------------------------
# Internal fields that should not be passed to EVChargingSession constructor
# ---------------------------------------------------------------------------

_INTERNAL_FIELDS = {"_status", "_row_index", "_error", "_matched_id"}


async def import_rows(
    rows: list[dict],
    selected_indices: set[int],
    duplicate_actions: dict[int, str],
    db_session: AsyncSession,
) -> dict:
    """Commit selected rows to the database and return result counts.

    Args:
        rows: List of transformed row dicts (from transform_rows / detect_duplicates).
        selected_indices: Set of _row_index values the user selected for import.
        duplicate_actions: Mapping of row_index -> action string ("skip", "insert", "update")
                           for rows that were detected as duplicates. New rows default to "insert".
        db_session: Active SQLAlchemy AsyncSession.

    Returns:
        Dict with keys: added, skipped, updated, failed.

    Partial success: each row is wrapped in a savepoint (SAVEPOINT via begin_nested).
    A failed row is rolled back to the savepoint and counted as failed; successful
    rows remain in the transaction. A single commit at the end persists all successes.
    """
    from db.models.charging_session import EVChargingSession

    added = 0
    skipped = 0
    updated = 0
    failed = 0

    for row in rows:
        row_index = row.get("_row_index", -1)

        # Row not selected — skip
        if row_index not in selected_indices:
            skipped += 1
            continue

        # Error rows cannot be imported
        if row.get("_status") == "error":
            failed += 1
            continue

        # Determine action: duplicate_actions overrides; new rows default to insert
        row_status = row.get("_status", "new")
        if row_index in duplicate_actions:
            action = duplicate_actions[row_index]
        elif row_status == "new":
            action = "insert"
        else:
            # duplicate/fuzzy_duplicate with no explicit action -> skip
            action = "skip"

        if action == "skip":
            skipped += 1
            continue

        # Strip internal fields before DB operations
        clean_row = {k: v for k, v in row.items() if k not in _INTERNAL_FIELDS}

        # Resolve network_id: may be a name string from CSV — convert to integer FK
        net_val = clean_row.get("network_id")
        if net_val is not None and not isinstance(net_val, int):
            try:
                clean_row["network_id"] = int(net_val)
            except (ValueError, TypeError):
                # It's a network name string — resolve via DB lookup/auto-create
                from web.queries.settings import resolve_network
                resolved_id = await resolve_network(
                    db_session, network_name=str(net_val)
                )
                if resolved_id:
                    clean_row["network_id"] = resolved_id
                else:
                    del clean_row["network_id"]

        if action == "insert":
            try:
                async with await db_session.begin_nested():
                    # Ensure device_id has a fallback (model requires NOT NULL)
                    if not clean_row.get("device_id"):
                        clean_row["device_id"] = "csv_import"
                    session_obj = EVChargingSession(**clean_row)
                    db_session.add(session_obj)
                added += 1
            except (IntegrityError, Exception):
                failed += 1

        elif action == "update":
            matched_id = row.get("_matched_id")
            if matched_id is None:
                failed += 1
                continue
            try:
                async with await db_session.begin_nested():
                    existing = await db_session.get(EVChargingSession, matched_id)
                    if existing is None:
                        failed += 1
                        continue
                    for field, value in clean_row.items():
                        if hasattr(existing, field):
                            setattr(existing, field, value)
                updated += 1
            except (IntegrityError, Exception):
                failed += 1

    await db_session.commit()

    return {"added": added, "skipped": skipped, "updated": updated, "failed": failed}
