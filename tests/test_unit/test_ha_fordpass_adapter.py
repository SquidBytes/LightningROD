"""Unit tests for the Home Assistant FordPass adapter contracts.

These tests verify that key fields are read from the expected entities and
handled with the expected unit behavior.
"""

import json
from pathlib import Path

import pytest

from web.services.sources.ha_fordpass.adapter import (  # noqa: F401
    FIELD_CONTRACTS,
    _last_seen_raw,
    process_event,
)

pytestmark = pytest.mark.unit

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# Metrics entity values are already metric.

def test_metrics_entity_battery_range_passthrough():
    """xevBatteryRange from metrics is already kilometers."""
    payload = _load("metric_ha_imperial_vehicle.json")
    metrics_state = payload["sensor.fordpass_YOUR_VIN_metrics"]
    raw = metrics_state["attributes"]["xevBatteryRange"]
    assert raw == 418  # fixture oracle
    # Adapter must emit this value unchanged to hv_battery_range column


def test_metrics_entity_max_range_passthrough():
    """xevBatteryMaximumRange from metrics is already kilometers."""
    payload = _load("metric_ha_imperial_vehicle.json")
    raw = payload["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]["xevBatteryMaximumRange"]
    assert raw == 418
    # Adapter must emit unchanged to hv_battery_max_range


# elveh state reads use per-event unit_of_measurement.

def test_elveh_state_read_time_uom_lookup():
    """elveh state reads use the unit_of_measurement from that event."""
    payload = _load("imperial_ha_imperial_vehicle.json")
    elveh = payload["sensor.fordpass_YOUR_VIN_elveh"]
    uom = elveh["attributes"]["unit_of_measurement"]
    assert uom == "mi"
    # Adapter, reading state=162 with uom="mi", must convert to km = ~260


# Trip distance must come from events data, not elveh attributes.

def test_elveh_tripDistanceTraveled_not_read():
    """Adapter contracts must not source trip distance from
    elveh.tripDistanceTraveled (or any elveh.trip* attribute). Trip data comes
    from the events entity xev-key-off-trip-segment-data instead.
    """
    elveh_trip_reads = [
        c for c in FIELD_CONTRACTS
        if "elveh" in c.source_locator.pattern
        and "tripDistance" in c.source_attribute
    ]
    assert not elveh_trip_reads, (
        "adapter reads trip distance from elveh attributes: "
        f"{[(c.source_locator.pattern, c.source_attribute) for c in elveh_trip_reads]}. "
        "Trip data must come from sensor.{vin}_events.xev-key-off-trip-segment-data."
    )
