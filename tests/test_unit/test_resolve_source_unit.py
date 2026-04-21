"""Tests for detection.resolve_source_unit — the unit resolver that drives
hass_processor's ingestion path after the hardcoded-default removal.

The resolver MUST never return a hardcoded default. Its priority chain:
    1. declared               — FIELD_CONTRACTS entry
    2. read_time_uom          — new_state.attributes.unit_of_measurement
    3. device_class_ha_config — attributes.device_class + ha_config.unit_system
    4. cross_reference        — prior detection cache
    5. unknown                — no signals; records reason bitstring
"""

from __future__ import annotations

import pytest

from web.services.units import detection


@pytest.fixture(autouse=True)
def _reset_detection():
    detection.clear()
    yield
    detection.clear()


# Common helpers ------------------------------------------------------------

_METRIC_CONFIG = {
    "unit_system": {
        "length": "km",
        "mass": "kg",
        "temperature": "\u00b0C",
        "volume": "L",
    }
}

_IMPERIAL_CONFIG = {
    "unit_system": {
        "length": "mi",
        "mass": "lb",
        "temperature": "\u00b0F",
        "volume": "gal",
    }
}

_FLAT_METRIC = {"unit_system": "metric"}
_FLAT_IMPERIAL = {"unit_system": "imperial"}


def _state(attributes: dict) -> dict:
    return {"state": "ok", "attributes": attributes}


# ---------------------------------------------------------------------------
# declared wins over everything else
# ---------------------------------------------------------------------------


def test_declared_wins_over_read_time_and_device_class():
    """A FIELD_CONTRACTS entry takes precedence even when the event carries
    conflicting UoM / device_class signals."""
    state = _state(
        {
            "unit_of_measurement": "mi",
            "device_class": "distance",
            "xevBatteryRange": 260,
        }
    )
    unit, method, confidence = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_metrics",
        attribute="xevBatteryRange",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="distance",
    )
    assert unit == "km"
    assert method == "declared"
    assert confidence == "high"


# ---------------------------------------------------------------------------
# read_time_uom — normalizes event UoM
# ---------------------------------------------------------------------------


def test_read_time_uom_resolves_mi_for_distance():
    state = _state({"unit_of_measurement": "mi"})
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="distance",
    )
    assert unit == "mi"
    assert method == "read_time_uom"


def test_read_time_uom_rejected_when_field_type_mismatches():
    """When the event's UoM is "mi" but the caller asks for a temperature,
    the resolver must not apply "mi" — it should fall through to
    device_class / ha_config instead."""
    state = _state({"unit_of_measurement": "mi"})  # distance UoM
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="tripAmbientTemp",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="temperature",
    )
    # "mi" is distance; cannot resolve a temp from it. ha_config.temperature
    # is imperial (°F) but no device_class -> resolver can't infer.
    assert unit is None
    assert method == "unknown"


# ---------------------------------------------------------------------------
# device_class + ha_config.unit_system
# ---------------------------------------------------------------------------


def test_device_class_temperature_metric_config_resolves_degC():
    state = _state({"device_class": "temperature"})
    unit, method, confidence = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="tripAmbientTemp",
        new_state=state,
        ha_config=_METRIC_CONFIG,
        field_type="temperature",
    )
    assert unit == "degC"
    assert method == "device_class_ha_config"
    assert confidence == "medium"


def test_device_class_temperature_imperial_config_resolves_degF():
    state = _state({"device_class": "temperature"})
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="tripAmbientTemp",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="temperature",
    )
    assert unit == "degF"
    assert method == "device_class_ha_config"


def test_device_class_distance_metric_resolves_km():
    state = _state({"device_class": "distance"})
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="someDistance",
        new_state=state,
        ha_config=_METRIC_CONFIG,
        field_type="distance",
    )
    assert unit == "km"
    assert method == "device_class_ha_config"


def test_device_class_distance_imperial_resolves_mi():
    state = _state({"device_class": "distance"})
    unit, _, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="someDistance",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="distance",
    )
    assert unit == "mi"


def test_device_class_speed_metric_resolves_kmh():
    state = _state({"device_class": "speed"})
    unit, _, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_vehicle",
        attribute="speed",
        new_state=state,
        ha_config=_METRIC_CONFIG,
        field_type="speed",
    )
    assert unit == "kmh"


def test_device_class_speed_imperial_resolves_mph():
    state = _state({"device_class": "speed"})
    unit, _, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_vehicle",
        attribute="speed",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="speed",
    )
    assert unit == "mph"


def test_device_class_pressure_returns_none():
    """Pressure is ambiguous across HA (hPa / psi / kPa); resolver must not
    guess. Documented out-of-scope for this pass."""
    state = _state({"device_class": "pressure"})
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_vehicle",
        attribute="tirePressure",
        new_state=state,
        ha_config=_METRIC_CONFIG,
        field_type="pressure",
    )
    assert unit is None
    assert method == "unknown"


def test_device_class_energy_always_kWh():
    state = _state({"device_class": "energy"})
    for cfg in (_METRIC_CONFIG, _IMPERIAL_CONFIG):
        unit, method, _ = detection.resolve_source_unit(
            entity_id="sensor.fordpass_ABC_energy",
            attribute="tripEnergyConsumed",
            new_state=state,
            ha_config=cfg,
            field_type="energy",
        )
        assert unit == "kWh"
        assert method == "device_class_ha_config"


def test_device_class_power_always_kW():
    state = _state({"device_class": "power"})
    for cfg in (_METRIC_CONFIG, _IMPERIAL_CONFIG):
        unit, method, _ = detection.resolve_source_unit(
            entity_id="sensor.fordpass_ABC_power",
            attribute="chargingPower",
            new_state=state,
            ha_config=cfg,
            field_type="power",
        )
        assert unit == "kW"
        assert method == "device_class_ha_config"


# ---------------------------------------------------------------------------
# cross_reference — prior cache entry fills in when current event has nothing
# ---------------------------------------------------------------------------


def test_cross_reference_fires_from_prior_detection():
    """A prior cross_reference entry for this source should resolve the unit
    even when the current event carries no signals of its own."""
    device_id = "VINXREF"
    # Seed a cross-reference detection for elveh.tripDistanceTraveled.
    detection.record_unknown(
        "sensor.fordpass_{vin}_elveh",
        "tripDistanceTraveled",
        11.8,
        reason="no_uom|no_device_class",
        device_id=device_id,
    )
    detection.try_cross_reference(
        device_id,
        "sensor.fordpass_{vin}_events",
        "xev-key-off-trip-segment-data.distance_traveled",
        19.0,
        "km",
    )

    # Now a NEW elveh event arrives with no signals at all.
    state = _state({"tripDistanceTraveled": 10.0})
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="tripDistanceTraveled",
        new_state=state,
        ha_config={},
        field_type="distance",
        record=False,
    )
    # Note: entity_id VIN ABC -> pattern sensor.fordpass_{vin}_elveh, matches
    # the seeded record.
    assert unit == "mi"
    assert method == "cross_reference"


# ---------------------------------------------------------------------------
# unknown — correct reason bitstring
# ---------------------------------------------------------------------------


def test_unknown_when_all_signals_fail():
    state = _state({})  # no uom, no device_class
    unit, method, confidence = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="someAttribute",
        new_state=state,
        ha_config={},
        field_type="distance",
    )
    assert unit is None
    assert method == "unknown"
    assert confidence == "low"

    snap = {(r.entity_pattern, r.attribute): r for r in detection.snapshot()}
    rec = snap[("sensor.fordpass_{vin}_elveh", "someAttribute")]
    reason = rec.unknown_reason or ""
    # Bitstring should cover every missing signal.
    assert "no_uom" in reason
    assert "no_device_class" in reason
    assert "no_unit_system" in reason
    assert "no_cross_ref" in reason


def test_unknown_reason_omits_bits_that_were_present():
    """When only `unit_of_measurement` is missing (but everything else was
    present), the reason should reflect that — not list every bit."""
    # device_class present, ha_config present, but device_class is "pressure"
    # (unsupported) so we end up unknown with a partial reason.
    state = _state({"device_class": "pressure"})
    _, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_tires",
        attribute="frontLeft",
        new_state=state,
        ha_config=_METRIC_CONFIG,
        field_type="pressure",
    )
    assert method == "unknown"
    snap = {(r.entity_pattern, r.attribute): r for r in detection.snapshot()}
    rec = snap[("sensor.fordpass_{vin}_tires", "frontLeft")]
    reason = rec.unknown_reason or ""
    assert "no_uom" in reason
    assert "no_device_class" not in reason  # device_class WAS present
    assert "no_unit_system" not in reason   # unit_system WAS present


# ---------------------------------------------------------------------------
# ha_config shape robustness
# ---------------------------------------------------------------------------


def test_flat_string_unit_system_metric():
    state = _state({"device_class": "temperature"})
    unit, _, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="tripAmbientTemp",
        new_state=state,
        ha_config=_FLAT_METRIC,
        field_type="temperature",
    )
    assert unit == "degC"


def test_flat_string_unit_system_imperial():
    state = _state({"device_class": "distance"})
    unit, _, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="someDist",
        new_state=state,
        ha_config=_FLAT_IMPERIAL,
        field_type="distance",
    )
    assert unit == "mi"


def test_missing_ha_config_produces_unknown():
    state = _state({"device_class": "temperature"})  # no UoM
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="tripAmbientTemp",
        new_state=state,
        ha_config={},  # no unit_system key
        field_type="temperature",
    )
    assert unit is None
    assert method == "unknown"


# ---------------------------------------------------------------------------
# No hardcoded defaults — None propagates all the way through
# ---------------------------------------------------------------------------


def test_no_hardcoded_default_on_total_signal_absence():
    """The resolver MUST return None when nothing resolves. It must never
    silently fall back to 'mi' or 'degF' or any other unit string."""
    state = _state({})
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="someField",
        new_state=state,
        ha_config={},
        field_type="distance",
    )
    assert unit is None, (
        f"resolver must not fabricate a default unit; got unit={unit!r} "
        f"method={method!r}"
    )


def test_convert_with_resolved_unit_returns_none_for_unknown():
    """convert_with_resolved_unit honours a None resolved_unit by skipping."""
    out = detection.convert_with_resolved_unit(
        raw_value=100.0,
        resolved_unit=None,
        method="unknown",
        confidence="low",
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="x",
    )
    assert out is None


def test_convert_with_resolved_unit_converts_known_unit():
    out = detection.convert_with_resolved_unit(
        raw_value=100.0,
        resolved_unit="mi",
        method="read_time_uom",
        confidence="high",
        entity_id="sensor.fordpass_ABC_elveh",
        attribute="x",
    )
    assert out == pytest.approx(160.934, abs=0.01)


# ---------------------------------------------------------------------------
# End-to-end: event with NO UoM but valid device_class + unit_system
# ---------------------------------------------------------------------------


def test_device_class_resolves_when_uom_absent_integration():
    """Integration check: event has NO unit_of_measurement BUT carries
    device_class='distance' and ha_config.unit_system is imperial. Resolver
    must resolve 'mi' via step 3 (no hardcoded fallback needed)."""
    state = _state({"device_class": "distance"})  # NO unit_of_measurement
    unit, method, _ = detection.resolve_source_unit(
        entity_id="sensor.fordpass_ABC_odometer",
        attribute="",
        new_state=state,
        ha_config=_IMPERIAL_CONFIG,
        field_type="distance",
        raw_value=12345,
        device_id="ABC",
    )
    assert unit == "mi"
    assert method == "device_class_ha_config"

    # Record should also surface on the diagnostic page.
    snap = {(r.entity_pattern, r.attribute): r for r in detection.snapshot()}
    rec = snap[("sensor.fordpass_{vin}_odometer", "")]
    assert rec.method == "device_class_ha_config"
    assert rec.detected_unit == "mi"
    assert rec.unknown_reason  # provenance string, not a failure bitstring
    assert "device_class='distance'" in rec.unknown_reason
