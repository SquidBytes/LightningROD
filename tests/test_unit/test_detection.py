"""Tests for web.services.units.detection."""

from __future__ import annotations

from datetime import timedelta

import pytest

from web.services.units import detection


@pytest.fixture(autouse=True)
def _reset_detection():
    detection.clear()
    yield
    detection.clear()


# ---------------------------------------------------------------------------
# record_declared
# ---------------------------------------------------------------------------


def test_record_declared_inserts_with_method_declared():
    detection.record_declared(
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        "km",
        260,
    )
    snap = detection.snapshot()
    assert len(snap) == 1
    rec = snap[0]
    assert rec.method == "declared"
    assert rec.confidence == "high"
    assert rec.detected_unit == "km"
    assert rec.sample_raw == 260.0


# ---------------------------------------------------------------------------
# record_read_time
# ---------------------------------------------------------------------------


def test_record_read_time_inserts_with_method_read_time_uom():
    detection.record_read_time(
        "sensor.fordpass_{vin}_elveh",
        "",
        "mi",
        162,
        device_id="VINX",
    )
    snap = detection.snapshot()
    assert len(snap) == 1
    rec = snap[0]
    assert rec.method == "read_time_uom"
    assert rec.confidence == "medium"
    assert rec.detected_unit == "mi"


# ---------------------------------------------------------------------------
# record_unknown
# ---------------------------------------------------------------------------


def test_record_unknown_inserts_with_none_unit_and_reason():
    detection.record_unknown(
        "sensor.fordpass_{vin}_soc",
        "batteryRange",
        195.0,
        reason="no unit_of_measurement on event",
        device_id="VINX",
    )
    snap = detection.snapshot()
    assert len(snap) == 1
    rec = snap[0]
    assert rec.method == "unknown"
    assert rec.detected_unit is None
    assert rec.unknown_reason == "no unit_of_measurement on event"


# ---------------------------------------------------------------------------
# try_cross_reference — distance
# ---------------------------------------------------------------------------


def test_cross_ref_distance_detects_mi():
    device_id = "VINMI"
    # Target read lands in the buffer first (from read_time or unknown).
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "",
        11.8,  # ~19 km in miles
        reason="no uom",
        device_id=device_id,
    )
    # Canonical events-entity reading arrives (already metric).
    updated = detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        19.0,
        "km",
    )
    assert len(updated) == 1
    rec = updated[0]
    assert rec.entity_pattern == "sensor.fordpass_{vin}_elveh"
    assert rec.attribute == ""
    assert rec.detected_unit == "mi"
    assert rec.method == "cross_reference"
    assert rec.ratio == pytest.approx(0.621, abs=0.01)


def test_cross_ref_distance_detects_km():
    device_id = "VINKM"
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "",
        19.0,  # already km
        reason="no uom",
        device_id=device_id,
    )
    updated = detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        19.0,
        "km",
    )
    assert len(updated) == 1
    rec = updated[0]
    assert rec.detected_unit == "km"
    assert rec.ratio == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# try_cross_reference — temperature
# ---------------------------------------------------------------------------


def test_cross_ref_temperature_detects_degF():
    device_id = "VINT_F"
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "tripAmbientTemp",
        77.0,  # ≈25°C expressed as °F
        reason="no uom",
        device_id=device_id,
    )
    updated = detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.ambient_temp",
        25.0,
        "degC",
    )
    assert len(updated) == 1
    rec = updated[0]
    assert rec.detected_unit == "degF"
    assert rec.method == "cross_reference"


def test_cross_ref_temperature_detects_degC():
    device_id = "VINT_C"
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "tripAmbientTemp",
        25.0,
        reason="no uom",
        device_id=device_id,
    )
    updated = detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.ambient_temp",
        25.0,
        "degC",
    )
    assert len(updated) == 1
    rec = updated[0]
    assert rec.detected_unit == "degC"
    assert rec.method == "cross_reference"


# ---------------------------------------------------------------------------
# resolve_unit priority: declared wins
# ---------------------------------------------------------------------------


def test_resolve_unit_declared_beats_cross_reference():
    device_id = "VINPRIO"
    # Record a cross-reference first
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "",
        19.0,
        reason="no uom",
        device_id=device_id,
    )
    detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        19.0,
        "km",
    )
    # Now declare the same source
    detection.record_declared(
        "sensor.fordpass_{vin}_elveh",
        "",
        "km",
        19.0,
    )
    assert detection.resolve_unit("sensor.fordpass_{vin}_elveh", "") == "km"
    snap = {
        (r.entity_pattern, r.attribute): r for r in detection.snapshot()
    }
    rec = snap[("sensor.fordpass_{vin}_elveh", "")]
    assert rec.method == "declared"


# ---------------------------------------------------------------------------
# Recent-reads buffer TTL
# ---------------------------------------------------------------------------


def test_recent_reads_buffer_ttl_drops_old_entries(monkeypatch):
    device_id = "VINTTL"
    # Insert an unknown reading.
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "",
        11.8,
        reason="no uom",
        device_id=device_id,
    )
    # Rewind the timestamp of the buffered entry into the far past.
    old_entries = detection._recent_reads[device_id]
    assert len(old_entries) == 1
    entity, attr, val, _ts = old_entries[0]
    detection._recent_reads[device_id] = [
        (entity, attr, val, _ts - timedelta(minutes=10))
    ]
    updated = detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_metrics",
        "xevBatteryRange",
        19.0,
        "km",
    )
    assert updated == [], "expired buffer entry should not produce a match"
