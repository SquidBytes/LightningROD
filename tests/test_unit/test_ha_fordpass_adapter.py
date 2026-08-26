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
    _metric_value,
    compute_trip_id,
    process_event,
)

pytestmark = pytest.mark.unit

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# Metrics entity values are already metric, and each one is value-wrapped.

def test_metrics_entity_battery_range_passthrough():
    """xevBatteryRange from metrics is already kilometers."""
    payload = _load("metric_ha_imperial_vehicle.json")
    metrics_state = payload["sensor.fordpass_YOUR_VIN_metrics"]
    raw = metrics_state["attributes"]["xevBatteryRange"]
    assert _metric_value(metrics_state["attributes"], "xevBatteryRange") == 260
    assert "updateTime" in raw, "metrics attributes arrive value-wrapped"
    # Adapter must emit this value unchanged to hv_battery_range column


def test_metrics_entity_max_range_passthrough():
    """xevBatteryMaximumRange from metrics is already kilometers."""
    payload = _load("metric_ha_imperial_vehicle.json")
    attrs = payload["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]
    assert _metric_value(attrs, "xevBatteryMaximumRange") == 418
    # Adapter must emit unchanged to hv_battery_max_range


def test_every_metrics_attribute_is_value_wrapped():
    """ha-fordpass passes Ford's metrics dict through verbatim, so every
    attribute on that entity is a {"value": ...} wrapper. A bare scalar here
    would let the adapter's unwrap regress unnoticed."""
    for fixture in sorted(p.name for p in FIXTURES_DIR.glob("*.json")):
        attrs = _load(fixture)["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]
        for key, value in attrs.items():
            if key in ("friendly_name", "icon"):
                continue  # injected by Home Assistant, not part of the metrics dict
            assert isinstance(value, dict) and "value" in value, (
                f"{fixture}: metrics attribute {key!r} is not value-wrapped"
            )


def test_metrics_attributes_are_identical_across_the_unit_matrix():
    """The metrics entity ignores Home Assistant's unit system entirely.

    ha-fordpass registers it as `attrs_fn=lambda data, units: get_metrics(data)`
    — the `units` argument is unused — so the same vehicle reading is the same
    number in all four fixtures. A per-fixture difference means someone
    unit-converted a raw passthrough, which is the double-conversion bug this
    matrix exists to catch.
    """
    # displaySystemOfMeasure reports how the vehicle is configured to display
    # values; it is a setting rather than a reading, and the matrix varies it
    # on purpose. Its own invariant is asserted below.
    varies_by_design = ("friendly_name", "icon", "displaySystemOfMeasure")
    baseline_name = "metric_ha_metric_vehicle.json"
    baseline = _load(baseline_name)["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]
    for fixture in sorted(p.name for p in FIXTURES_DIR.glob("*.json")):
        if fixture == baseline_name:
            continue
        attrs = _load(fixture)["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]
        for key, value in baseline.items():
            if key in varies_by_design:
                continue
            assert attrs.get(key) == value, (
                f"{fixture}: metrics attribute {key!r} is {attrs.get(key)!r}, "
                f"but {baseline_name} has {value!r} — metrics are unit-invariant"
            )


def test_display_system_of_measure_tracks_the_vehicle_half_of_the_matrix():
    """Fixture names read `{ha_unit_system}_ha_{vehicle_display}_vehicle`.

    Both halves vary independently, so a fixture that mirrored HA's system
    here would quietly collapse the matrix to two cases instead of four.
    """
    for fixture in sorted(p.name for p in FIXTURES_DIR.glob("*.json")):
        vehicle_display = fixture.split("_ha_")[1].removesuffix("_vehicle.json")
        attrs = _load(fixture)["sensor.fordpass_YOUR_VIN_metrics"]["attributes"]
        assert attrs["displaySystemOfMeasure"]["value"] == vehicle_display.upper(), (
            f"{fixture}: displaySystemOfMeasure should follow the vehicle "
            f"display setting ({vehicle_display.upper()})"
        )


# elveh state reads use per-event unit_of_measurement.

def test_elveh_state_read_time_uom_lookup():
    """elveh state reads use the unit_of_measurement from that event."""
    payload = _load("imperial_ha_imperial_vehicle.json")
    elveh = payload["sensor.fordpass_YOUR_VIN_elveh"]
    uom = elveh["attributes"]["unit_of_measurement"]
    assert uom == "mi"
    # Adapter, reading state=162 with uom="mi", must convert to km = ~260


# Elveh unit-bearing trip/range attributes are HA-unit-system localized
# (ha-fordpass fordpass_handler.py routes them through localize_distance),
# so their contracts must resolve from ha_config.unit_system — never from
# the elveh state's unit_of_measurement, which tracks the vehicle display
# system and caused the duplicate-trip double-conversion bug.

def test_elveh_distance_contracts_are_ha_localized():
    elveh_distance_attrs = {
        "tripDistanceTraveled",
        "tripEfficiency",
        "tripRangeRegenerated",
        "maximumBatteryRange",
    }
    seen = set()
    for c in FIELD_CONTRACTS:
        if "elveh" not in c.source_locator.pattern:
            continue
        if c.source_attribute in elveh_distance_attrs:
            seen.add(c.source_attribute)
            assert c.ha_unit_system_converted, (
                f"elveh.{c.source_attribute} must be ha_unit_system_converted=True; "
                "resolving it via the elveh state uom double-converts for "
                "imperial-display vehicles on metric HA"
            )
    assert seen == elveh_distance_attrs, f"missing elveh contracts: {elveh_distance_attrs - seen}"


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


# Duration canonicalization — events emit seconds; elveh emits str(timedelta)
# ("0:41:18") on current ha-fordpass, bare minutes on older builds.

def test_duration_canonicalized_to_seconds_from_elveh():
    from web.services.sources.ha_fordpass.handlers import _duration_to_seconds

    assert _duration_to_seconds("0:41:18") == 2478.0
    assert _duration_to_seconds("1:00:00") == 3600.0
    assert _duration_to_seconds("1 day, 2:03:04") == 93784.0
    # Legacy numeric minutes
    assert _duration_to_seconds(25) == 1500.0
    assert _duration_to_seconds("25") == 1500.0


def test_duration_to_seconds_handles_invalid():
    from web.services.sources.ha_fordpass.handlers import _duration_to_seconds

    assert _duration_to_seconds(None) is None
    assert _duration_to_seconds("") is None
    assert _duration_to_seconds("not:a:time") is None
