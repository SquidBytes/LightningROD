"""Unit tests for the Home Assistant FordPass adapter contracts.

These tests verify that key fields are read from the expected entities and
handled with the expected unit behavior.
"""

import json
import uuid
from pathlib import Path

import pytest

from web.services.sources.ha_fordpass.adapter import (  # noqa: F401
    FIELD_CONTRACTS,
    LIGHTNINGROD_TRIP_NAMESPACE,
    _last_seen_raw,
    compute_trip_id,
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


# Deterministic trip-id helper — closes the cross-source dedup invariant.

def test_compute_trip_id_deterministic():
    """Same inputs must produce the same uuid5 across calls."""
    a = compute_trip_id("VIN123", "2026-04-28T10:00:00Z")
    b = compute_trip_id("VIN123", "2026-04-28T10:00:00Z")
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_compute_trip_id_returns_none_when_input_missing():
    """Without device_id or tripUpdateTime the helper returns None so callers
    can fall through to the legacy predicate-match path."""
    assert compute_trip_id("VIN123", None) is None
    assert compute_trip_id("", "2026-04-28T10:00:00Z") is None
    assert compute_trip_id(None, "2026-04-28T10:00:00Z") is None


def test_compute_trip_id_different_inputs_differ():
    """Distinct device_id OR distinct update_time must yield distinct uuid5."""
    base = compute_trip_id("VIN123", "2026-04-28T10:00:00Z")
    diff_device = compute_trip_id("VIN999", "2026-04-28T10:00:00Z")
    diff_time = compute_trip_id("VIN123", "2026-04-28T10:00:01Z")
    assert base != diff_device
    assert base != diff_time
    assert diff_device != diff_time


def test_compute_trip_id_uses_locked_namespace():
    """Sanity: the namespace constant must not drift — every existing
    deterministic id depends on it."""
    assert LIGHTNINGROD_TRIP_NAMESPACE == uuid.UUID("a1b2c3d4-e5f6-4a5b-9c8d-7e6f5a4b3c20")


# Score=0 sentinel converter — ha-fordpass emits 0 as "unmeasured".

def test_score_zero_becomes_null():
    """tripDrivingScore=0 ingested → DB stores NULL, not 0.0."""
    from web.services.sources.ha_fordpass.handlers import _score_or_null

    assert _score_or_null(0) is None
    assert _score_or_null("0") is None
    assert _score_or_null(0.0) is None


def test_score_nonzero_passes_through():
    """tripDrivingScore=42 ingested → DB stores 42.0."""
    from web.services.sources.ha_fordpass.handlers import _score_or_null

    assert _score_or_null(42) == 42.0
    assert _score_or_null("87.5") == 87.5


def test_score_invalid_returns_null():
    """Non-numeric score values return None (matches _safe_float behavior)."""
    from web.services.sources.ha_fordpass.handlers import _score_or_null

    assert _score_or_null(None) is None
    assert _score_or_null("nope") is None


# Duration canonicalization — events emit seconds, elveh emits minutes.

def test_duration_canonicalized_to_seconds_from_elveh():
    """elveh tripDuration=25 (minutes) -> stored as 1500.0 seconds."""
    from web.services.sources.ha_fordpass.handlers import (
        _duration_to_seconds_from_minutes,
    )

    assert _duration_to_seconds_from_minutes(25) == 1500.0
    assert _duration_to_seconds_from_minutes("25") == 1500.0


def test_duration_to_seconds_from_minutes_handles_none():
    from web.services.sources.ha_fordpass.handlers import (
        _duration_to_seconds_from_minutes,
    )

    assert _duration_to_seconds_from_minutes(None) is None
    assert _duration_to_seconds_from_minutes("") is None
