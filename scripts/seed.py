"""Seed charging sessions from CSV into PostgreSQL.

Usage:
    uv run python scripts/seed.py --vin YOUR_VIN
    uv run python scripts/seed.py --vin YOUR_VIN --dry-run
    uv run python scripts/seed.py --vin YOUR_VIN --csv-path data/2026_charging_sessions_master.csv
"""

import argparse
import asyncio
import csv
import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure project root is on path so db/config imports work
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.engine import AsyncSessionLocal
from db.models.charging_session import EVChargingSession

# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------

FREE_LOCATIONS = {
    "Work",
    "Dealership",
}

WORK_LOCATIONS = {"Work"}
HOME_LOCATIONS = {"Home"}

# Columns to update on conflict (all mapped columns except id, session_id, ingested_at)
UPDATABLE_COLUMNS = [
    "device_id",
    "charge_type",
    "location_name",
    "location_type",
    "is_free",
    "session_start_utc",
    "session_end_utc",
    "charge_duration_seconds",
    "energy_kwh",
    "charging_kw",
    "max_power",
    "min_power",
    "start_soc",
    "end_soc",
    "cost",
    "cost_without_overrides",
    "miles_added",
    "charging_voltage",
    "charging_amperage",
    "is_complete",
    "recorded_at",
    "source_system",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def parse_uuid(v: str) -> Optional[uuid.UUID]:
    """Parse a UUID string, returning None if empty or invalid."""
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return uuid.UUID(v)
    except (ValueError, AttributeError):
        return None


def str_or_none(v: str) -> Optional[str]:
    """Return stripped string or None if empty."""
    v = v.strip() if v else ""
    return v if v else None


def float_or_none(v: str) -> Optional[float]:
    """Return float or None if empty/invalid."""
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_bool(v: str) -> bool:
    """Return True for 'True'/'1'/'true', False otherwise."""
    return v.strip().lower() in ("true", "1", "yes") if v else False


def parse_timestamp(v: str) -> Optional[datetime]:
    """Parse ISO timestamp string to timezone-aware datetime.

    Python 3.11+ fromisoformat handles both offset-aware and naive formats.
    If result is naive, treat as UTC.
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


def normalize_charge_type(charger_type: str, location_name: str) -> Optional[str]:
    """Normalize charger type to 'AC' or 'DC'.

    Rules:
    - 'AC Level 2' or 'AC_BASIC' → 'AC'
    - 'DC_FAST' → 'DC'
    - empty + location is "Other Public" → 'AC'
    - Otherwise None
    """
    ct = charger_type.strip() if charger_type else ""
    if ct in ("AC Level 2", "AC_BASIC", "level_2", "ac_charging", "AC Level 1"):
        return "AC"
    if ct in ("DC_FAST", "dc_fast", "dc_charging"):
        return "DC"
    # Fallback for work location with no charger type
    loc = location_name.strip() if location_name else ""
    if not ct and loc == "Work":
        return "AC"
    return None


def classify_location_type(location_name: str) -> str:
    """Classify location as 'home', 'work', or 'public'."""
    loc = location_name.strip() if location_name else ""
    if loc in HOME_LOCATIONS or loc.lower() == "home":
        return "home"
    if loc in WORK_LOCATIONS:
        return "work"
    return "public"


def classify_is_free(location_name: str) -> bool:
    """Return True if the location is a free charging location."""
    loc = location_name.strip() if location_name else ""
    return loc in FREE_LOCATIONS


def make_session_id(
    start_time: Optional[datetime],
    location_name: Optional[str],
    energy_kwh: Optional[float],
) -> uuid.UUID:
    """Generate a deterministic UUID from session fields using MD5."""
    start_str = start_time.isoformat() if start_time else ""
    loc_str = location_name or ""
    kwh_str = str(energy_kwh) if energy_kwh is not None else ""
    key = f"{start_str}|{loc_str}|{kwh_str}"
    return uuid.UUID(bytes=hashlib.md5(key.encode()).digest())


# ---------------------------------------------------------------------------
# CSV column mapping
# ---------------------------------------------------------------------------

COLUMN_MAP = {
    "session_id": ("session_id", parse_uuid),
    "location_name": ("location_name", str_or_none),
    "start_time": ("session_start_utc", parse_timestamp),
    "end_time": ("session_end_utc", parse_timestamp),
    "duration_minutes": (
        "charge_duration_seconds",
        lambda v: float(v.strip()) * 60 if v.strip() else None,
    ),
    "energy_consumed_kwh": ("energy_kwh", float_or_none),
    "average_power_kw": ("charging_kw", float_or_none),
    "max_power": ("max_power", float_or_none),
    "min_power": ("min_power", float_or_none),
    "start_soc_percent": ("start_soc", float_or_none),
    "end_soc_percent": ("end_soc", float_or_none),
    "cost_total": ("cost", float_or_none),
    "cost_without_overrides": ("cost_without_overrides", float_or_none),
    "miles_added": ("miles_added", float_or_none),
    "charging_voltage": ("charging_voltage", float_or_none),
    "charging_amperage": ("charging_amperage", float_or_none),
    "is_complete": ("is_complete", parse_bool),
    "recorded_at": ("recorded_at", parse_timestamp),
}


# ---------------------------------------------------------------------------
# LubeLogger gap-fill
# ---------------------------------------------------------------------------


def load_lubelogger(ll_path: str) -> dict:
    """Load LubeLogger CSV into a lookup dict keyed by (date_str, normalized_location).

    Returns empty dict if file does not exist.
    """
    if not os.path.exists(ll_path):
        print(f"  LubeLogger file not found at {ll_path} — skipping gap-fill")
        return {}

    lookup: dict = {}
    with open(ll_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_raw = row.get("Date", "").strip()
            if not date_raw:
                continue
            try:
                date_str = datetime.strptime(date_raw, "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue

            # Normalize location"
            location_norm = row.get("extrafield_ChargeLocation", "").strip()

            key = (date_str, location_norm)
            lookup[key] = row

    print(f"  Loaded {len(lookup)} LubeLogger entries for gap-fill")
    return lookup


def apply_lubelogger_gap_fill(
    row_dict: dict,
    ll_date_str: str,
    ll_lookup: dict,
) -> dict:
    """Attempt to fill missing energy_kwh or session_start_utc from LubeLogger data."""
    location_name = row_dict.get("location_name") or ""
    key = (ll_date_str, location_name)
    ll_row = ll_lookup.get(key)

    if ll_row is None:
        return row_dict

    # Fill energy_kwh if missing
    if row_dict.get("energy_kwh") is None:
        kwh_raw = ll_row.get("extrafield_EnergyKWh", "").strip()
        row_dict["energy_kwh"] = float_or_none(kwh_raw)

    # Fill session_start_utc if missing
    if row_dict.get("session_start_utc") is None:
        ts_raw = ll_row.get("extrafield_SessionTimestamp", "").strip()
        row_dict["session_start_utc"] = parse_timestamp(ts_raw)

    # Fill charge_duration_seconds if missing
    if row_dict.get("charge_duration_seconds") is None:
        dur_raw = ll_row.get("extrafield_DurationSec", "").strip()
        if dur_raw:
            try:
                row_dict["charge_duration_seconds"] = float(dur_raw)
            except ValueError:
                pass

    # Fill charge_type if missing
    if row_dict.get("charge_type") is None:
        ct_raw = ll_row.get("extrafield_ChargerType", "").strip()
        row_dict["charge_type"] = normalize_charge_type(ct_raw, location_name)

    return row_dict


# ---------------------------------------------------------------------------
# Row transformation
# ---------------------------------------------------------------------------


def transform_row(
    csv_row: dict,
    vin: str,
    ll_lookup: dict,
) -> Optional[dict]:
    """Transform a single CSV row into a DB-ready dict.

    Returns None if the row should be skipped (missing core fields after gap-fill).
    """
    # Apply COLUMN_MAP to build initial DB dict
    db_row: dict[str, Any] = {}
    for csv_col, (db_col, transform_fn) in COLUMN_MAP.items():
        raw = csv_row.get(csv_col, "")
        db_row[db_col] = transform_fn(raw)

    # Charger type from CSV (before computed fields)
    csv_charger_type = csv_row.get("charger_type", "")
    location_name = db_row.get("location_name") or ""

    # Attempt LubeLogger gap-fill if core fields are missing
    needs_gap_fill = db_row.get("energy_kwh") is None or db_row.get("session_start_utc") is None
    if needs_gap_fill and ll_lookup:
        start = db_row.get("session_start_utc")
        if start is not None:
            ll_date_str = start.strftime("%Y-%m-%d")
        else:
            # Try to extract date from start_time CSV field directly
            start_raw = csv_row.get("start_time", "").strip()
            if start_raw:
                try:
                    dt = datetime.fromisoformat(start_raw)
                    ll_date_str = dt.strftime("%Y-%m-%d")
                except ValueError:
                    ll_date_str = ""
            else:
                ll_date_str = ""

        if ll_date_str:
            db_row = apply_lubelogger_gap_fill(db_row, ll_date_str, ll_lookup)

    # Skip rows still missing core fields after gap-fill
    if db_row.get("energy_kwh") is None and db_row.get("session_start_utc") is None:
        print(
            f"  SKIP: Row missing energy_kwh and session_start_utc after gap-fill "
            f"(location={location_name!r})"
        )
        return None

    # Computed / overridden fields
    db_row["device_id"] = vin
    db_row["source_system"] = "csv_seed"
    db_row["charge_type"] = normalize_charge_type(csv_charger_type, location_name)
    db_row["location_type"] = classify_location_type(location_name)
    db_row["is_free"] = classify_is_free(location_name)

    # Handle session_id: use CSV value if valid UUID, else generate deterministic UUID
    session_id = db_row.get("session_id")
    if session_id is None:
        session_id = make_session_id(
            db_row.get("session_start_utc"),
            location_name or None,
            db_row.get("energy_kwh"),
        )
        db_row["session_id"] = session_id

    # Ensure is_complete is never None (model has nullable=False, default=False)
    if db_row.get("is_complete") is None:
        db_row["is_complete"] = False

    return db_row


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------


def load_csv(csv_path: str) -> list[dict]:
    """Load master CSV and return list of raw row dicts."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


async def seed(args: argparse.Namespace) -> None:
    """Main async seed function."""
    csv_path = args.csv_path
    ll_path = args.ll_path
    vin = args.vin
    dry_run = args.dry_run

    # --- Load master CSV ---
    print(f"\nLoading CSV: {csv_path}")
    raw_rows = load_csv(csv_path)
    print(f"  Loaded {len(raw_rows)} rows from CSV")

    # --- Load LubeLogger for gap-fill ---
    print(f"\nLoading LubeLogger: {ll_path}")
    ll_lookup = load_lubelogger(ll_path)

    # --- Transform rows ---
    print("\nTransforming rows...")
    transformed: list[dict] = []
    skipped = 0
    for raw_row in raw_rows:
        db_row = transform_row(raw_row, vin, ll_lookup)
        if db_row is None:
            skipped += 1
        else:
            transformed.append(db_row)

    print(f"  Transformed {len(transformed)} rows ({skipped} skipped)")

    if not transformed:
        print("\nNo rows to insert. Exiting.")
        return

    # --- Dry run ---
    if dry_run:
        print("\n[DRY RUN] Would insert the following rows (sample of first 5):")
        for row in transformed[:5]:
            session_id = row.get("session_id")
            start = row.get("session_start_utc")
            kwh = row.get("energy_kwh")
            loc = row.get("location_name")
            ct = row.get("charge_type")
            lt = row.get("location_type")
            free = row.get("is_free")
            print(
                f"  session_id={session_id} start={start} kwh={kwh} "
                f"loc={loc!r} charge_type={ct} location_type={lt} is_free={free}"
            )
        print(f"\n[DRY RUN] Total rows that would be upserted: {len(transformed)}")
        return

    # --- Database upsert ---
    print(f"\nUpserting {len(transformed)} rows...")
    async with AsyncSessionLocal() as session:
        stmt = pg_insert(EVChargingSession).values(transformed)
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id"],
            set_={col: stmt.excluded[col] for col in UPDATABLE_COLUMNS},
        )
        await session.execute(stmt)
        await session.commit()
    print("  Upsert complete.")

    # --- Post-seed verification ---
    print("\nVerification results:")
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS session_count,
                    ROUND(SUM(energy_kwh)::numeric, 3) AS total_kwh,
                    COUNT(*) FILTER (WHERE charge_type = 'AC') AS ac_count,
                    COUNT(*) FILTER (WHERE charge_type = 'DC') AS dc_count,
                    COUNT(*) FILTER (WHERE location_type = 'home') AS home_count,
                    COUNT(*) FILTER (WHERE location_type = 'work') AS work_count,
                    COUNT(*) FILTER (WHERE location_type = 'public') AS public_count,
                    COUNT(*) FILTER (WHERE is_free = true) AS free_count,
                    COUNT(*) FILTER (WHERE is_free = false) AS paid_count
                FROM ev_charging_session
                WHERE source_system = 'csv_seed'
                """
            )
        )
        row = result.fetchone()

    session_count = int(row.session_count)
    total_kwh = float(row.total_kwh) if row.total_kwh is not None else 0.0
    ac_count = int(row.ac_count)
    dc_count = int(row.dc_count)
    home_count = int(row.home_count)
    work_count = int(row.work_count)
    public_count = int(row.public_count)
    free_count = int(row.free_count)
    paid_count = int(row.paid_count)

    expected_count = len(transformed)

    print(f"  {'Metric':<30} {'Actual':>10}  {'Expected':>10}")
    print(f"  {'-'*52}")
    print(f"  {'Session count':<30} {session_count:>10}  {expected_count:>10}")
    print(f"  {'Total kWh':<30} {total_kwh:>10.3f}  {'~4749.334':>10}")
    print(f"  {'AC sessions':<30} {ac_count:>10}  {'~188':>10}")
    print(f"  {'DC sessions':<30} {dc_count:>10}  {'~15':>10}")
    print(f"  {'Home sessions':<30} {home_count:>10}  {'~32':>10}")
    print(f"  {'Work sessions':<30} {work_count:>10}  {'~149':>10}")
    print(f"  {'Public sessions':<30} {public_count:>10}  {'~22':>10}")
    print(f"  {'Free sessions':<30} {free_count:>10}")
    print(f"  {'Paid sessions':<30} {paid_count:>10}")

    if session_count != expected_count:
        print(
            f"\nWARNING: session_count ({session_count}) does not match "
            f"transformed row count ({expected_count}). "
            f"Some rows may have been previously inserted with a different source_system."
        )
    else:
        print(f"\n  All {session_count} sessions verified in database.")

    if skipped > 0:
        print(f"\n  NOTE: {skipped} rows were skipped due to missing core fields.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed charging sessions from CSV into PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python scripts/seed.py --vin YOUR_VIN
  uv run python scripts/seed.py --vin YOUR_VIN --dry-run
  uv run python scripts/seed.py --vin YOUR_VIN --csv-path data/custom.csv
        """,
    )
    parser.add_argument(
        "--csv-path",
        default="data/2026_charging_sessions_master.csv",
        help="Path to master charging sessions CSV (default: data/2026_charging_sessions_master.csv)",
    )
    parser.add_argument(
        "--ll-path",
        default="data/2026-02-27_lubeLogger_fuel.csv",
        help="Path to LubeLogger fuel CSV for gap-fill (default: data/2026-02-27_lubeLogger_fuel.csv)",
    )
    parser.add_argument(
        "--vin",
        required=True,
        help="Vehicle VIN — used as device_id for all sessions",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and transform but do not write to database",
    )
    args = parser.parse_args()
    asyncio.run(seed(args))


if __name__ == "__main__":
    main()
