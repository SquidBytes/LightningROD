"""Unit tests for temperature-vs-efficiency scatter builder. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.unit


def test_phase_25_temp_scatter_empty_below_min_points():
    """build_temperature_correlation_chart returns '' when len(data) < min_points."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


def test_phase_25_temp_scatter_derived_efficiency_from_distance_energy():
    """Efficiency comes from distance/energy_consumed, not the stored efficiency column."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


def test_phase_25_temp_scatter_us_units_converts_celsius_to_fahrenheit():
    """When distance_unit='us', ambient_temp is converted from °C to °F before plotting."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


def test_phase_25_temp_scatter_metric_units_passes_celsius_through():
    """When distance_unit='metric', ambient_temp passes through as °C."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


def test_phase_25_temp_scatter_includes_trendline_trace():
    """Output HTML contains a second Scatter trace named 'Trend' from np.polyfit(deg=1)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")
