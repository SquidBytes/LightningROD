"""Unit tests for synthetic charge curve taper math. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.unit


def test_phase_25_synthesize_curve_plateau_below_threshold():
    """synthesize_curve returns max_kw for every SOC <= plateau_soc."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


def test_phase_25_synthesize_curve_linear_taper_above_threshold():
    """synthesize_curve linearly interpolates from max_kw to max_kw*tail_fraction across plateau_soc..100."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


def test_phase_25_synthesize_curve_endpoint_value():
    """At SOC=100 the returned kW equals max_kw * tail_fraction (default 0.20)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


def test_phase_25_synthesize_curve_monotonic_non_increasing():
    """Curve never increases as SOC increases (plateau then decay)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


def test_phase_25_build_synthetic_charge_curve_chart_empty_data_returns_empty_string():
    """build_synthetic_charge_curve_chart returns '' when session_count == 0."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")
