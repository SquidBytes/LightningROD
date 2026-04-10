"""Unit tests for regen recovery dual-axis bar chart. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.unit


def test_phase_25_regen_bar_empty_input_returns_empty_string():
    """build_regen_recovery_chart([]) returns ''."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")


def test_phase_25_regen_bar_includes_bar_trace_and_line_trace():
    """Output HTML contains both a Bar trace (regen kWh) and a Scatter trace (regen %)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")


def test_phase_25_regen_bar_uses_secondary_y_axis():
    """The Scatter (regen %) trace is bound to secondary_y=True via make_subplots."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")


def test_phase_25_regen_bar_regen_pct_derivation():
    """regen_pct == regen_kwh / gross_kwh * 100 for each trip input row."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")
