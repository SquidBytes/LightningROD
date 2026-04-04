"""Tests for web/unit_system.py conversion and label module."""

import pytest

from web.unit_system import (
    DISTANCE_UNITS,
    KM_PER_MI,
    MI_PER_KM,
    TEMP_UNITS,
    convert_distance,
    convert_efficiency,
    convert_fuel_efficiency,
    convert_fuel_volume,
    convert_speed,
    convert_temp,
    get_units,
    to_metric_distance,
    to_metric_fuel_efficiency,
    to_metric_fuel_volume,
    to_metric_temp,
)


# ---------------------------------------------------------------------------
# Label dict
# ---------------------------------------------------------------------------


class TestGetUnits:
    def test_default_us(self):
        units = get_units("us", "us")
        assert units["distance_label"] == "mi"
        assert units["efficiency_label"] == "mi/kWh"
        assert units["fuel_efficiency_label"] == "MPG"
        assert units["fuel_volume_label"] == "gal"
        assert units["temp_label"] == "\u00b0F"
        assert units["speed_label"] == "mph"

    def test_default_metric(self):
        units = get_units("metric", "metric")
        assert units["distance_label"] == "km"
        assert units["efficiency_label"] == "km/kWh"
        assert units["fuel_efficiency_label"] == "L/100km"
        assert units["fuel_volume_label"] == "L"
        assert units["temp_label"] == "\u00b0C"
        assert units["speed_label"] == "km/h"

    def test_mixed_us_distance_metric_temp(self):
        units = get_units("us", "metric")
        assert units["distance_label"] == "mi"
        assert units["efficiency_label"] == "mi/kWh"
        assert units["temp_label"] == "\u00b0C"

    def test_mixed_metric_distance_us_temp(self):
        units = get_units("metric", "us")
        assert units["distance_label"] == "km"
        assert units["temp_label"] == "\u00b0F"

    def test_invalid_falls_back_to_us(self):
        units = get_units("bogus", "nope")
        assert units["distance_label"] == "mi"
        assert units["temp_label"] == "\u00b0F"


# ---------------------------------------------------------------------------
# Outbound conversions (metric DB -> display)
# ---------------------------------------------------------------------------


class TestConvertDistance:
    def test_us_converts_km_to_miles(self):
        assert convert_distance(100, "us") == pytest.approx(62.1371, abs=0.001)

    def test_metric_passthrough(self):
        assert convert_distance(100, "metric") == 100.0

    def test_none_returns_none(self):
        assert convert_distance(None, "us") is None
        assert convert_distance(None, "metric") is None

    def test_zero(self):
        assert convert_distance(0, "us") == 0.0
        assert convert_distance(0, "metric") == 0.0


class TestConvertEfficiency:
    def test_us_km_per_kwh_to_mi_per_kwh(self):
        # 5 km/kWh -> ~3.107 mi/kWh
        assert convert_efficiency(5.0, "us") == pytest.approx(3.10686, abs=0.001)

    def test_metric_passthrough(self):
        assert convert_efficiency(5.0, "metric") == 5.0

    def test_none(self):
        assert convert_efficiency(None, "us") is None


class TestConvertFuelEfficiency:
    def test_us_l100km_to_mpg(self):
        # 9.4086 L/100km ≈ 25 MPG
        assert convert_fuel_efficiency(9.4086, "us") == pytest.approx(25.0, abs=0.01)

    def test_metric_passthrough(self):
        assert convert_fuel_efficiency(8.0, "metric") == 8.0

    def test_none(self):
        assert convert_fuel_efficiency(None, "us") is None

    def test_zero_returns_none(self):
        assert convert_fuel_efficiency(0, "us") is None


class TestConvertFuelVolume:
    def test_us_liters_to_gallons(self):
        # 56.78 L ≈ 15 gal
        assert convert_fuel_volume(56.78115, "us") == pytest.approx(15.0, abs=0.001)

    def test_metric_passthrough(self):
        assert convert_fuel_volume(40.0, "metric") == 40.0


class TestConvertSpeed:
    def test_us_kmh_to_mph(self):
        assert convert_speed(100, "us") == pytest.approx(62.1371, abs=0.001)

    def test_metric_passthrough(self):
        assert convert_speed(100, "metric") == 100.0


class TestConvertTemp:
    def test_us_celsius_to_fahrenheit(self):
        assert convert_temp(0, "us") == pytest.approx(32.0, abs=0.001)
        assert convert_temp(100, "us") == pytest.approx(212.0, abs=0.001)
        assert convert_temp(20, "us") == pytest.approx(68.0, abs=0.001)

    def test_metric_passthrough(self):
        assert convert_temp(20, "metric") == 20.0

    def test_none(self):
        assert convert_temp(None, "us") is None


# ---------------------------------------------------------------------------
# Inbound conversions (display input -> metric DB)
# ---------------------------------------------------------------------------


class TestToMetricDistance:
    def test_us_miles_to_km(self):
        assert to_metric_distance(10, "us") == pytest.approx(16.0934, abs=0.001)

    def test_metric_passthrough(self):
        assert to_metric_distance(10, "metric") == 10.0


class TestToMetricFuelEfficiency:
    def test_us_mpg_to_l100km(self):
        # 25 MPG -> 9.4086 L/100km
        assert to_metric_fuel_efficiency(25, "us") == pytest.approx(9.4086, abs=0.001)

    def test_metric_passthrough(self):
        assert to_metric_fuel_efficiency(8.0, "metric") == 8.0

    def test_zero_returns_none(self):
        assert to_metric_fuel_efficiency(0, "us") is None


class TestToMetricFuelVolume:
    def test_us_gallons_to_liters(self):
        assert to_metric_fuel_volume(15, "us") == pytest.approx(56.78115, abs=0.001)

    def test_metric_passthrough(self):
        assert to_metric_fuel_volume(40, "metric") == 40.0


class TestToMetricTemp:
    def test_us_fahrenheit_to_celsius(self):
        assert to_metric_temp(32, "us") == pytest.approx(0.0, abs=0.001)
        assert to_metric_temp(212, "us") == pytest.approx(100.0, abs=0.001)
        assert to_metric_temp(68, "us") == pytest.approx(20.0, abs=0.001)

    def test_metric_passthrough(self):
        assert to_metric_temp(20, "metric") == 20.0


# ---------------------------------------------------------------------------
# Round-trip tests — convert then to_metric should return original
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @pytest.mark.parametrize("unit", ["us", "metric"])
    @pytest.mark.parametrize("value", [0.0, 10.0, 100.0, 1234.567])
    def test_distance_roundtrip(self, unit, value):
        display = convert_distance(value, unit)
        back = to_metric_distance(display, unit)
        # MI_PER_KM is truncated, so allow small drift for US path
        assert back == pytest.approx(value, rel=1e-4, abs=0.001)

    @pytest.mark.parametrize("unit", ["us", "metric"])
    @pytest.mark.parametrize("value", [-20.0, 0.0, 20.0, 100.0])
    def test_temp_roundtrip(self, unit, value):
        display = convert_temp(value, unit)
        back = to_metric_temp(display, unit)
        assert back == pytest.approx(value, abs=0.0001)

    @pytest.mark.parametrize("unit", ["us", "metric"])
    @pytest.mark.parametrize("value", [5.0, 8.5, 15.0])
    def test_fuel_efficiency_roundtrip(self, unit, value):
        display = convert_fuel_efficiency(value, unit)
        back = to_metric_fuel_efficiency(display, unit)
        assert back == pytest.approx(value, abs=0.0001)

    @pytest.mark.parametrize("unit", ["us", "metric"])
    @pytest.mark.parametrize("value", [10.0, 40.0, 100.0])
    def test_fuel_volume_roundtrip(self, unit, value):
        display = convert_fuel_volume(value, unit)
        back = to_metric_fuel_volume(display, unit)
        assert back == pytest.approx(value, abs=0.0001)
