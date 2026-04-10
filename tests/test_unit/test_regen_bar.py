"""Unit tests for regen recovery dual-axis bar chart. phase_25."""
import pytest

from web.queries.driving_performance import build_regen_recovery_chart

pytestmark = pytest.mark.unit


def _rows():
    return [
        {
            "trip_num": 1,
            "start_time": None,
            "distance": 100.0,
            "energy_consumed": 20.0,
            "range_regenerated": 10.0,
            "regen_kwh": 2.0,
            "regen_pct": 10.0,
        },
        {
            "trip_num": 2,
            "start_time": None,
            "distance": 50.0,
            "energy_consumed": 10.0,
            "range_regenerated": 5.0,
            "regen_kwh": 1.0,
            "regen_pct": 10.0,
        },
        {
            "trip_num": 3,
            "start_time": None,
            "distance": 200.0,
            "energy_consumed": 40.0,
            "range_regenerated": 20.0,
            "regen_kwh": 4.0,
            "regen_pct": 10.0,
        },
    ]


def test_phase_25_regen_bar_empty_input_returns_empty_string():
    """build_regen_recovery_chart([]) returns ''."""
    assert build_regen_recovery_chart([]) == ""


def test_phase_25_regen_bar_includes_bar_trace_and_line_trace():
    """Output HTML contains both a Bar trace (regen kWh) and a Scatter trace (regen %)."""
    html = build_regen_recovery_chart(_rows())
    assert html
    # Bar trace for regen kWh
    assert '"type": "bar"' in html or '"type":"bar"' in html
    # Scatter trace for regen %
    assert '"type": "scatter"' in html or '"type":"scatter"' in html


def test_phase_25_regen_bar_uses_secondary_y_axis():
    """The Scatter (regen %) trace is bound to secondary_y=True via make_subplots."""
    html = build_regen_recovery_chart(_rows())
    assert html
    # Plotly's make_subplots with secondary_y=True produces yaxis2 in the layout
    assert "yaxis2" in html


def test_phase_25_regen_bar_regen_pct_derivation():
    """regen_pct == regen_kwh / energy_consumed * 100 for each trip input row.

    Sanity test — validates the _rows() fixture is consistent with the locked
    derivation formula (range_regenerated / (distance / energy_consumed) → kWh,
    then kWh / energy_consumed * 100 → pct).
    """
    rows = _rows()
    for r in rows:
        expected_kwh = r["range_regenerated"] / (r["distance"] / r["energy_consumed"])
        expected_pct = (expected_kwh / r["energy_consumed"]) * 100.0
        assert r["regen_kwh"] == pytest.approx(expected_kwh)
        assert r["regen_pct"] == pytest.approx(expected_pct)
