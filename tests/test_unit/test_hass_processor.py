"""Pure function unit tests for hass_processor.

Tests unit conversions, slug extraction, value parsing, and other pure functions
that do NOT require a database connection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web.services.hass_processor import (
    extract_slug,
    fahrenheit_to_celsius,
    get_device_id,
    miles_to_km,
    normalize_value,
    wh_to_kwh,
    _safe_float,
    _normalize_charge_type,
    _format_address,
    _parse_iso_datetime,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Unit conversion tests
# ---------------------------------------------------------------------------


def test_miles_to_km():
    assert miles_to_km(100) == pytest.approx(160.934, abs=0.001)


def test_miles_to_km_zero():
    assert miles_to_km(0) == 0.0


def test_fahrenheit_to_celsius():
    assert fahrenheit_to_celsius(212) == pytest.approx(100.0, abs=0.01)


def test_fahrenheit_to_celsius_freezing():
    assert fahrenheit_to_celsius(32) == pytest.approx(0.0, abs=0.01)


def test_wh_to_kwh():
    assert wh_to_kwh(1000) == pytest.approx(1.0, abs=0.001)


def test_wh_to_kwh_fractional():
    assert wh_to_kwh(2500) == pytest.approx(2.5, abs=0.001)


# ---------------------------------------------------------------------------
# normalize_value tests
# ---------------------------------------------------------------------------


def test_normalize_value_miles_imperial():
    """FordPass reports miles -> convert to km."""
    result = normalize_value(100, "mi", {"_fordpass_distance_unit": "mi"})
    assert result == pytest.approx(160.934, abs=0.001)


def test_normalize_value_miles_metric_passthrough():
    """FordPass reports km -> skip conversion, pass through."""
    result = normalize_value(100, "mi", {"_fordpass_distance_unit": "km"})
    assert result == 100.0


def test_normalize_value_miles_default_fallback():
    """No FordPass unit info -> default to metric, pass through."""
    result = normalize_value(100, "mi", {})
    assert result == 100.0


def test_normalize_value_fahrenheit_imperial():
    """FordPass reports degF -> convert to Celsius."""
    result = normalize_value(212, "degF", {"_fordpass_temp_unit": "degF"})
    assert result == pytest.approx(100.0, abs=0.01)


def test_normalize_value_fahrenheit_metric_passthrough():
    """FordPass reports degC -> skip conversion, pass through."""
    result = normalize_value(100, "degF", {"_fordpass_temp_unit": "degC"})
    assert result == 100.0


def test_normalize_value_fahrenheit_default_fallback():
    """No FordPass temp info -> default to metric, pass through."""
    result = normalize_value(212, "degF", {})
    assert result == 212.0


def test_normalize_value_wh():
    """Wh conversion unchanged by FordPass units."""
    result = normalize_value(5000, "Wh", {})
    assert result == pytest.approx(5.0, abs=0.001)


def test_normalize_value_metric_passthrough():
    """Metric values pass through unchanged."""
    result = normalize_value(42.0, "km", {"_fordpass_distance_unit": "km"})
    assert result == 42.0


def test_normalize_value_none():
    result = normalize_value(None, "mi", {})
    assert result is None


def test_normalize_value_invalid():
    result = normalize_value("not_a_number", "mi", {})
    assert result is None


# ---------------------------------------------------------------------------
# Slug extraction tests
# ---------------------------------------------------------------------------


def test_extract_slug_soc():
    assert extract_slug("sensor.fordpass_1ftvw1el6pwg05841_soc") == "soc"


def test_extract_slug_odometer():
    assert extract_slug("sensor.fordpass_1ftvw1el6pwg05841_odometer") == "odometer"


def test_extract_slug_elveh():
    assert extract_slug("sensor.fordpass_1ftvw1el6pwg05841_elveh") == "elveh"


def test_extract_slug_no_match():
    assert extract_slug("sensor.temperature_living_room") is None


def test_extract_slug_empty():
    assert extract_slug("") is None


def test_extract_slug_none():
    assert extract_slug(None) is None


# ---------------------------------------------------------------------------
# get_device_id tests
# ---------------------------------------------------------------------------


def test_get_device_id_from_entity():
    result = get_device_id("sensor.fordpass_1ftvw1el6pwg05841_soc", {})
    assert result == "1ftvw1el6pwg05841"


def test_get_device_id_override():
    result = get_device_id("sensor.fordpass_abc_soc", {"_vin_override": "OVERRIDE_VIN"})
    assert result == "OVERRIDE_VIN"


def test_get_device_id_unknown():
    result = get_device_id("sensor.weather_temp", {})
    assert result == "unknown"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_safe_float():
    assert _safe_float("42.5") == 42.5
    assert _safe_float(42) == 42.0
    assert _safe_float(None) is None
    assert _safe_float("not_a_number") is None


def test_normalize_charge_type():
    # DC variants collapse to "DC"
    assert _normalize_charge_type("DC_FAST") == "DC"
    assert _normalize_charge_type("DC_COMBO") == "DC"
    assert _normalize_charge_type("DCFC") == "DC"
    assert _normalize_charge_type("DC") == "DC"
    # AC variants (all levels) collapse to "AC"
    assert _normalize_charge_type("AC_LEVEL_2") == "AC"
    assert _normalize_charge_type("AC_BASIC") == "AC"
    assert _normalize_charge_type("AC_LEVEL_1") == "AC"
    assert _normalize_charge_type("Level 2") == "AC"
    assert _normalize_charge_type("AC") == "AC"
    # Empty / None
    assert _normalize_charge_type(None) is None
    assert _normalize_charge_type("") is None
    assert _normalize_charge_type("   ") is None
    # Unknown values fall through uppercased
    assert _normalize_charge_type("CUSTOM_TYPE") == "CUSTOM_TYPE"


def test_format_address():
    addr = {"address1": "123 Main St", "city": "Portland", "state": "OR"}
    assert _format_address(addr) == "123 Main St, Portland, OR"


def test_format_address_partial():
    addr = {"city": "Portland", "state": "OR"}
    assert _format_address(addr) == "Portland, OR"


def test_format_address_none():
    assert _format_address(None) is None
    assert _format_address({}) is None


def test_parse_iso_datetime():

    result = _parse_iso_datetime("2025-06-15T12:00:00Z")
    assert result is not None
    assert result.year == 2025
    assert result.month == 6
    assert result.tzinfo is not None


def test_parse_iso_datetime_none():
    assert _parse_iso_datetime(None) is None
    assert _parse_iso_datetime("") is None


# ---------------------------------------------------------------------------
# Phase 27-01: Charging-session thermal context tests
#
# These exercise the energytransferlogentry handler with a mocked db. We skip
# the duplicate-detection branch by omitting session_start_utc, and stub
# resolve_location / resolve_network so the handler path reduces to
# "parse attrs -> db.add(EVChargingSession)". The asserts run against the
# single EVChargingSession instance captured from `db.add`.
# ---------------------------------------------------------------------------


def _make_fake_db():
    """Return an AsyncMock db whose `add` captures the session instance."""
    db = AsyncMock()
    db.add = MagicMock()  # sync method on real session
    # db.execute is only reachable on paths we intentionally skip, but stub it anyway
    exec_result = MagicMock()
    exec_result.all.return_value = []
    exec_result.scalar_one_or_none.return_value = None
    db.execute.return_value = exec_result
    return db


def _charging_payload(**overrides):
    """Build an energytransferlogentry-shaped payload for handler tests.

    Omits `energyTransferDuration` so duplicate detection is skipped.
    """
    attrs = {
        "energyConsumed": 23.5,
        "chargerType": "AC_BASIC",
        "stateOfCharge": {"firstSOC": 56.0, "lastSOC": 80.0},
        "power": {"max": 7446.4, "min": 0.0, "weightedAverage": 6628.9},
        "plugDetails": {"totalPluggedInTime": 11538, "totalDistanceAdded": 80.0},
        # location deliberately omitted -> resolve_location returns None path
    }
    attrs.update(overrides)
    return {"state": "complete", "attributes": attrs}


@pytest.mark.asyncio
async def test_charging_session_persists_temp_fields():
    """batteryTemperature + outsidetemp (°F) -> mirrored °C on start/end columns."""
    from web.services.hass_processor import handle_energy_transfer

    payload = _charging_payload(batteryTemperature=77.0, outsidetemp=72.05)
    db = _make_fake_db()
    ha_config = {"_fordpass_temp_unit": "degF"}

    with patch(
        "web.queries.locations.resolve_location", new=AsyncMock(return_value=None)
    ):
        await handle_energy_transfer(
            "energytransferlogentry", payload, ha_config, "TESTVIN001", db
        )

    assert db.add.call_count == 1, "Expected exactly one db.add(session)"
    session = db.add.call_args[0][0]

    # 77°F = 25°C, 72.05°F ≈ 22.25°C
    assert session.battery_temp_start == pytest.approx(25.0, abs=0.01)
    assert session.battery_temp_end == pytest.approx(25.0, abs=0.01)
    assert session.ambient_temp_start == pytest.approx(22.25, abs=0.01)
    assert session.ambient_temp_end == pytest.approx(22.25, abs=0.01)


@pytest.mark.asyncio
async def test_charging_session_missing_temp_fields_is_ok():
    """No batteryTemperature/outsidetemp keys -> columns stay None, no raise."""
    from web.services.hass_processor import handle_energy_transfer

    payload = _charging_payload()  # no temp keys
    db = _make_fake_db()
    ha_config = {"_fordpass_temp_unit": "degF"}

    with patch(
        "web.queries.locations.resolve_location", new=AsyncMock(return_value=None)
    ):
        await handle_energy_transfer(
            "energytransferlogentry", payload, ha_config, "TESTVIN001", db
        )

    assert db.add.call_count == 1
    session = db.add.call_args[0][0]
    assert session.battery_temp_start is None
    assert session.battery_temp_end is None
    assert session.ambient_temp_start is None
    assert session.ambient_temp_end is None
