"""Unit tests for AC/DC charge-type donut builder. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.unit


def test_phase_25_donut_kwh_metric_returns_html():
    """build_charge_type_donut_chart(metric='kwh') returns non-empty HTML for valid data."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 AC/DC donut task")


def test_phase_25_donut_count_metric_returns_html():
    """build_charge_type_donut_chart(metric='count') returns non-empty HTML."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 AC/DC donut task")


def test_phase_25_donut_cost_metric_returns_html():
    """build_charge_type_donut_chart(metric='cost') returns non-empty HTML."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 AC/DC donut task")


def test_phase_25_donut_empty_input_returns_empty_string():
    """build_charge_type_donut_chart([]) returns ''."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 AC/DC donut task")


def test_phase_25_donut_unknown_bucket_hidden_when_zero():
    """Rows with charge_type='Unknown' and zero value are filtered from the chart output."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 AC/DC donut task")


def test_phase_25_donut_unknown_bucket_shown_when_nonzero():
    """Rows with charge_type='Unknown' and nonzero value appear in chart output."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 AC/DC donut task")
