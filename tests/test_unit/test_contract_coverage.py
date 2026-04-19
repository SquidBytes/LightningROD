"""Invariant: every unit-ful DB column has >= 1 FIELD_CONTRACTS entry.

D-E2. Adding a new unit-ful column without a contract fails CI. Explicit
exemption list documents every skipped column with justification.

MUST fail today — FIELD_CONTRACTS module does not exist yet.
"""

import pytest

pytestmark = pytest.mark.unit

# Exemption list — each entry is (table, column).
# Rule: a column is exempt ONLY if its value is not a physical quantity that
# requires unit conversion on ingestion (e.g. currency, counts, percentages,
# raw enumerations, already-SI voltage/amperage/power). Add new entries with a
# one-line justification comment above each grouping.
_EXEMPTIONS: set[tuple[str, str]] = {
    # SOC is a percentage, dimensionless
    ("ev_battery_status", "hv_battery_soc"),
    ("ev_battery_status", "hv_battery_actual_soc"),
    # Voltage/amperage/power already stored in SI (V, A, kW) from source
    ("ev_battery_status", "hv_battery_voltage"),
    ("ev_battery_status", "hv_battery_amperage"),
    ("ev_battery_status", "hv_battery_kw"),
    ("ev_battery_status", "hv_battery_capacity"),
    ("ev_battery_status", "motor_voltage"),
    ("ev_battery_status", "motor_amperage"),
    ("ev_battery_status", "motor_kw"),
    ("ev_battery_status", "lv_battery_level"),
    ("ev_battery_status", "lv_battery_voltage"),
    # Scores are 0-100 dimensionless
    ("ev_trip_metrics", "driving_score"),
    ("ev_trip_metrics", "speed_score"),
    ("ev_trip_metrics", "acceleration_score"),
    ("ev_trip_metrics", "deceleration_score"),
    ("ev_trip_metrics", "electrical_efficiency"),
    ("ev_trip_metrics", "brake_torque"),
    # duration is seconds (SI) — not converted
    ("ev_trip_metrics", "duration"),
    # Charging session numeric columns already in SI or currency:
    ("ev_charging_session", "charging_voltage"),
    ("ev_charging_session", "charging_amperage"),
    ("ev_charging_session", "charging_kw"),
}


# Columns that MUST have a FIELD_CONTRACTS entry.
# Sourced from db/models/trip_metrics.py, charging_session.py, battery_status.py.
# Update this set when 29-01 / 29-02 confirm the final list.
_UNIT_FUL_COLUMNS: set[tuple[str, str]] = {
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


def test_all_unit_ful_columns_have_contracts():
    from web.services.sources.ha_fordpass.adapter import FIELD_CONTRACTS
    covered = {(c.target_db_table, c.target_db_column) for c in FIELD_CONTRACTS}
    missing = _UNIT_FUL_COLUMNS - covered - _EXEMPTIONS
    assert not missing, (
        f"Unit-ful columns without a FIELD_CONTRACT entry: {missing}. "
        "Add the contract in adapter.py OR add the column to _EXEMPTIONS "
        "with justification."
    )
