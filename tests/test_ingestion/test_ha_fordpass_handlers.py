"""Pure function unit tests for ha_fordpass.handlers.
Tests slug extraction, device_id resolution, value parsing, address formatting,
and other pure helper functions that do NOT require a database connection.
cleanup: tests for the deleted legacy value-normalizer helper and
its mi/degF/Wh convenience wrappers have been removed because the behavioral
surface they covered is now owned by `tests/test_unit/test_to_metric.py`.
The 2026-03-21 auto-detect unit system logic is gone ; see
``.
"""

import pytest

from web.services.ingestion._helpers import _safe_float
from web.services.sources.ha_fordpass.handlers import (
    _format_address,
    _normalize_charge_type,
    _parse_iso_datetime,
    extract_slug,
    get_device_id,
)

pytestmark = pytest.mark.unit


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
