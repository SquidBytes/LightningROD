"""ha-fordpass adapter contract tests (D-E3).

One test per FIELD_CONTRACTS entry: load fixture, call adapter.process_event,
assert the DB write lands with the expected metric value.

MUST fail collection today — web.services.sources.ha_fordpass.adapter does
not exist yet. Tests come online after Plan 29-02 lands the adapter.
"""

import json
from pathlib import Path

import pytest

from web.services.sources.ha_fordpass.adapter import (  # noqa: F401
    FIELD_CONTRACTS,
    process_event,
    _last_seen_raw,
)

pytestmark = pytest.mark.unit

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# --- D-B1: metrics entity always metric ---

def test_metrics_entity_battery_range_passthrough():
    """xevBatteryRange is always km per D-B1. No conversion on ingestion."""
    payload = _load("metric_ha_imperial_vehicle.json")
    metrics_state = payload["sensor.fordpass_YOUR_VIN_metrics"]
    raw = metrics_state["attributes"]["xevBatteryRange"]
    assert raw == 418  # fixture oracle
    # Adapter must emit this value unchanged to hv_battery_range column


def test_metrics_entity_max_range_passthrough():
    """xevBatteryMaximumRange is always km per D-B1."""
    payload = _load("metric_ha_imperial_vehicle.json")
    raw = payload["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]["xevBatteryMaximumRange"]
    assert raw == 418
    # Adapter must emit unchanged to hv_battery_max_range


# --- D-B3: main-state fallback reads unit_of_measurement per event ---

def test_elveh_state_read_time_uom_lookup():
    """elveh main state — per D-B3, read unit_of_measurement from the event itself.
    Never from a process-global flag.
    """
    payload = _load("imperial_ha_imperial_vehicle.json")
    elveh = payload["sensor.fordpass_YOUR_VIN_elveh"]
    uom = elveh["attributes"]["unit_of_measurement"]
    assert uom == "mi"
    # Adapter, reading state=162 with uom="mi", must convert to km = ~260


# --- D-B4: elveh attributes are NOT read ---

def test_elveh_tripDistanceTraveled_not_read():
    """D-B4: adapter must NOT read tripDistanceTraveled from elveh attributes.
    Trip data comes from `events` entity xev-key-off-trip-segment-data instead.
    """
    # No direct assertion possible until adapter exists; test is a named lock.
    # Filled out in 29-02 with a behavior assertion.
    pass


# Placeholder: one real test per FIELD_CONTRACTS entry will be added in 29-02
# once the concrete contract list is authored. Today we lock the import + the
# key D-B1/D-B3/D-B4 behaviors via named tests above.
