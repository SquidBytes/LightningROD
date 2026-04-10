"""Unit tests for AC/DC charge-type donut builder. phase_25 Wave 2."""
import pytest

from web.queries.energy import build_charge_type_donut_chart

pytestmark = pytest.mark.unit


def _fixture_rows():
    return [
        {"charge_type": "AC", "kwh": 100.0, "session_count": 10, "total_cost": 15.50},
        {"charge_type": "DC", "kwh": 300.0, "session_count": 5, "total_cost": 60.00},
    ]


def test_phase_25_donut_kwh_metric_returns_html():
    """build_charge_type_donut_chart(metric='kwh') returns non-empty HTML for valid data."""
    html = build_charge_type_donut_chart(_fixture_rows(), metric="kwh")
    assert html
    assert '"type": "pie"' in html or '"type":"pie"' in html


def test_phase_25_donut_count_metric_returns_html():
    """build_charge_type_donut_chart(metric='count') returns non-empty HTML."""
    html = build_charge_type_donut_chart(_fixture_rows(), metric="count")
    assert html
    # Session counts 10 and 5 are embedded in the serialized values array
    assert "10" in html and "5" in html


def test_phase_25_donut_cost_metric_returns_html():
    """build_charge_type_donut_chart(metric='cost') returns non-empty HTML."""
    html = build_charge_type_donut_chart(_fixture_rows(), metric="cost")
    assert html
    assert "15.5" in html or "15.50" in html


def test_phase_25_donut_empty_input_returns_empty_string():
    """build_charge_type_donut_chart([]) returns ''."""
    assert build_charge_type_donut_chart([], metric="kwh") == ""
    assert build_charge_type_donut_chart([], metric="count") == ""
    assert build_charge_type_donut_chart([], metric="cost") == ""


def test_phase_25_donut_unknown_bucket_hidden_when_zero():
    """Rows with charge_type='Unknown' and zero value are filtered from the chart output."""
    rows = [
        {"charge_type": "AC", "kwh": 10.0, "session_count": 1, "total_cost": 1.0},
        {"charge_type": "Unknown", "kwh": 0.0, "session_count": 0, "total_cost": 0.0},
    ]
    html = build_charge_type_donut_chart(rows, metric="kwh")
    assert html
    assert "Unknown" not in html


def test_phase_25_donut_unknown_bucket_shown_when_nonzero():
    """Rows with charge_type='Unknown' and nonzero value appear in chart output."""
    rows = [
        {"charge_type": "AC", "kwh": 10.0, "session_count": 1, "total_cost": 1.0},
        {"charge_type": "Unknown", "kwh": 5.0, "session_count": 1, "total_cost": 0.5},
    ]
    html = build_charge_type_donut_chart(rows, metric="kwh")
    assert html
    assert "Unknown" in html
