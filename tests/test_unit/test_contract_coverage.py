"""Ensure every unit-aware DB column has a matching field contract.

The test discovers columns from SQLAlchemy metadata so new columns are
automatically checked without editing a hard-coded list.
"""

import pytest

import db.models.battery_status  # noqa: F401
import db.models.charging_session  # noqa: F401
import db.models.trip_metrics  # noqa: F401
import db.models.vehicle_status  # noqa: F401

# Trigger model imports so Base.metadata is fully populated.
from db.models import Base  # re-exported from db.models.__init__

pytestmark = pytest.mark.unit

# Tables this contract-coverage invariant applies to.
# Other tables (ev_vehicles, ev_location, reference data, etc.) are out of
# scope because they are not populated through the ha_fordpass adapter.
_WATCHED_TABLES: set[str] = {
    "ev_trip_metrics",
    "ev_charging_session",
    "ev_battery_status",
    "ev_vehicle_status",
}

# Column-name substrings that indicate a unit-ful physical quantity.
# A column whose name contains ANY of these tokens is considered unit-ful
# unless it is listed in _EXEMPTIONS below. Keep this list tight — adding a
# too-broad suffix will enrol unrelated columns and force spurious
# exemption entries.
_UNIT_FUL_TOKENS: tuple[str, ...] = (
    "range",        # hv_battery_range, hv_battery_max_range, range_regenerated
    "distance",     # distance, distance_added
    "energy_consumed",  # trip energy
    "temp",         # ambient_temp, cabin_temp, battery_temp_start, outside_air_temp
    "temperature",  # hv_battery_temperature (redundant superset of 'temp' but documents intent)
    "efficiency",   # trip efficiency
)

# Explicit exemption list. Each entry MUST carry a justification comment.
# Rule: a column is exempt ONLY if its value is not a physical quantity
# that requires unit conversion on ingestion (percentages, voltages already
# SI, scores, counts, currency, version tags).
_EXEMPTIONS: set[tuple[str, str]] = {
    # Scores are 0-100 dimensionless
    ("ev_trip_metrics", "driving_score"),
    ("ev_trip_metrics", "speed_score"),
    ("ev_trip_metrics", "acceleration_score"),
    ("ev_trip_metrics", "deceleration_score"),
    # electrical_efficiency is a dimensionless trip-score field (0-100) per
    # ha-fordpass; matches 'efficiency' token but is NOT a unit-ful quantity.
    ("ev_trip_metrics", "electrical_efficiency"),
    # Already-SI scalar columns that don't match any token would not need
    # exemptions, but we keep the SI groupings for documentation parity.
}


def _is_unit_ful(column_name: str) -> bool:
    lc = column_name.lower()
    return any(tok in lc for tok in _UNIT_FUL_TOKENS)


def _discover_unit_ful_columns() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for table_name, table in Base.metadata.tables.items():
        if table_name not in _WATCHED_TABLES:
            continue
        for col in table.columns:
            if _is_unit_ful(col.name):
                out.add((table_name, col.name))
    return out


def test_watched_tables_are_present_in_metadata():
    """Guardrail: if a watched table is missing from metadata (e.g. model
    file not imported), the invariant becomes silently vacuous. Fail loud.
    """
    missing = _WATCHED_TABLES - set(Base.metadata.tables.keys())
    assert not missing, f"Watched tables missing from metadata: {missing}"


def test_all_unit_ful_columns_have_contracts():
    from web.services.sources.ha_fordpass.adapter import FIELD_CONTRACTS
    discovered = _discover_unit_ful_columns()
    covered = {(c.target_db_table, c.target_db_column) for c in FIELD_CONTRACTS}
    missing = discovered - covered - _EXEMPTIONS
    assert not missing, (
        f"Unit-ful columns without a FIELD_CONTRACT entry: {missing}\n"
        f"Discovered {len(discovered)} unit-ful columns across "
        f"{_WATCHED_TABLES}, {len(covered)} have contracts, "
        f"{len(_EXEMPTIONS)} exempt.\n"
        "Fix: add the contract in web/services/sources/ha_fordpass/adapter.py, "
        "OR add the column to _EXEMPTIONS here with a justification comment."
    )


def test_exemptions_only_apply_to_watched_tables():
    """Protect against stale exemption entries surviving a table rename."""
    for table, col in _EXEMPTIONS:
        assert table in _WATCHED_TABLES, (
            f"Exemption {(table, col)} references non-watched table '{table}'. "
            "Either remove the exemption or add the table to _WATCHED_TABLES."
        )


def test_discovery_finds_expected_minimum_set():
    """Sanity: ensure the token-based discovery captures the known
    unit-ful columns. If discovery regresses (e.g. a token gets dropped),
    this test fails loudly before the coverage check can silently pass.
    """
    discovered = _discover_unit_ful_columns()
    must_find = {
        ("ev_trip_metrics", "distance"),
        ("ev_trip_metrics", "energy_consumed"),
        ("ev_trip_metrics", "efficiency"),
        ("ev_trip_metrics", "range_regenerated"),
        ("ev_trip_metrics", "ambient_temp"),
        ("ev_trip_metrics", "cabin_temp"),
        ("ev_trip_metrics", "outside_air_temp"),
        ("ev_charging_session", "distance_added"),
        ("ev_charging_session", "battery_temp_start"),
        ("ev_charging_session", "battery_temp_end"),
        ("ev_charging_session", "ambient_temp_start"),
        ("ev_charging_session", "ambient_temp_end"),
        ("ev_battery_status", "hv_battery_range"),
        ("ev_battery_status", "hv_battery_max_range"),
        ("ev_battery_status", "hv_battery_temperature"),
    }
    missing = must_find - discovered
    assert not missing, (
        f"Token-based discovery missed expected unit-ful columns: {missing}. "
        "Review _UNIT_FUL_TOKENS."
    )
