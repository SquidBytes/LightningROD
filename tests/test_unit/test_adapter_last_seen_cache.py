"""Cache behavior tests for ``ha_fordpass.adapter._last_seen_raw``.

These tests cover key format, overwrite behavior, and cache clearing.
"""

from datetime import UTC, datetime

import pytest

from web.services.sources.ha_fordpass import adapter
from web.services.units.contracts import FieldContract

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure each test starts and ends with an empty cache."""
    adapter._last_seen_raw.clear()
    yield
    adapter._last_seen_raw.clear()


def _sample_contract() -> FieldContract:
    return FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="xevBatteryMaximumRange",
        source_unit="km",
        target_db_table="ev_battery_status",
        target_db_column="hv_battery_max_range",
        target_unit="km",
        notes="",
    )


def test_record_last_seen_writes_key():
    c = _sample_contract()
    adapter._record_last_seen(c, raw_value=418.0, converted=418.0)

    key = f"{c.source_entity_pattern}|{c.source_attribute}"
    assert key in adapter._last_seen_raw
    entry = adapter._last_seen_raw[key]
    assert entry["value"] == 418.0
    assert entry["unit"] == "km"
    assert entry["converted"] == 418.0
    # seen_at is ISO8601 UTC and close to now.
    parsed = datetime.fromisoformat(entry["seen_at"])
    assert parsed.tzinfo is not None
    delta = abs((datetime.now(UTC) - parsed).total_seconds())
    assert delta < 5, f"seen_at should be within 5s of now, got delta={delta}s"


def test_record_last_seen_updates_existing_key():
    c = _sample_contract()
    adapter._record_last_seen(c, raw_value=100, converted=100)
    adapter._record_last_seen(c, raw_value=200, converted=200)

    key = f"{c.source_entity_pattern}|{c.source_attribute}"
    # Only one entry per key, and it holds the latest value.
    assert len(adapter._last_seen_raw) == 1
    assert adapter._last_seen_raw[key]["value"] == 200
    assert adapter._last_seen_raw[key]["converted"] == 200


def test_record_last_seen_effective_unit_overrides_contract_unit():
    """read-time fallback must record the effective unit, not the
    contract default. This keeps /admin/data-sources honest for
    elveh-shaped contracts that resolve UoM per event.
    """
    c = FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_elveh",
        source_attribute="tripEfficiency",
        source_unit="km",
        target_db_table="ev_trip_metrics",
        target_db_column="efficiency",
        target_unit="km",
        notes="",
    )
    adapter._record_last_seen(c, raw_value=2.5, converted=4.02, effective_unit="mi")
    key = f"{c.source_entity_pattern}|{c.source_attribute}"
    entry = adapter._last_seen_raw[key]
    assert entry["unit"] == "mi"
    assert entry["value"] == 2.5
    assert entry["converted"] == 4.02


def test_clear_cache_empties():
    c = _sample_contract()
    adapter._record_last_seen(c, raw_value=1, converted=1)
    assert adapter._last_seen_raw  # populated
    adapter._last_seen_raw.clear()
    assert adapter._last_seen_raw == {}


def test_ha_unit_system_converted_imperial_resolves_to_mi():
    """A contract flagged `ha_unit_system_converted=True` on imperial HA
    must resolve the effective source unit to `mi` and convert 64 -> ~103 km.
    """
    c = FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="plugDetails.totalDistanceAdded",
        source_unit="km",  # declared default only — ha_config wins
        target_db_table="ev_charging_session",
        target_db_column="distance_added",
        target_unit="km",
        ha_unit_system_converted=True,
    )
    unit, method = adapter._resolve_source_unit(
        c, new_state=None, ha_config={"unit_system": "imperial"}
    )
    assert unit == "mi"
    assert method == "ha_unit_system_converted"

    converted = adapter.convert(
        c, raw_value=64, new_state=None, ha_config={"unit_system": "imperial"}
    )
    assert converted == pytest.approx(103.0, abs=0.5)


def test_ha_unit_system_converted_metric_stays_km():
    """Same contract on metric HA resolves to km and passes the value through."""
    c = FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="plugDetails.totalDistanceAdded",
        source_unit="km",
        target_db_table="ev_charging_session",
        target_db_column="distance_added",
        target_unit="km",
        ha_unit_system_converted=True,
    )
    unit, method = adapter._resolve_source_unit(
        c, new_state=None, ha_config={"unit_system": "metric"}
    )
    assert unit == "km"
    assert method == "ha_unit_system_converted"

    converted = adapter.convert(
        c, raw_value=103, new_state=None, ha_config={"unit_system": "metric"}
    )
    assert converted == pytest.approx(103.0, abs=0.5)


def test_ha_unit_system_converted_missing_config_falls_back():
    """Contract flagged ha_unit_system_converted but ha_config is absent —
    resolver must emit method='declared_fallback' using the declared unit,
    so ingestion still produces a value instead of dropping the field.
    """
    c = FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_energytransferlogentry",
        source_attribute="plugDetails.totalDistanceAdded",
        source_unit="km",
        target_db_table="ev_charging_session",
        target_db_column="distance_added",
        target_unit="km",
        ha_unit_system_converted=True,
    )
    unit, method = adapter._resolve_source_unit(c, new_state=None, ha_config=None)
    assert unit == "km"
    assert method == "declared_fallback"


def test_key_convention_matches_contract():
    """Key format must be f'{entity_pattern}|{attribute}'.

    /admin/data-sources builds the same key when displaying cached values;
    any drift here silently breaks the diagnostic page.
    """
    c = _sample_contract()
    adapter._record_last_seen(c, raw_value=42, converted=42)
    expected_key = "sensor.fordpass_{vin}_metrics|xevBatteryMaximumRange"
    assert expected_key in adapter._last_seen_raw
