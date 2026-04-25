"""Unit tests for synthetic charge curve taper math. phase_25 Wave 2."""
import pytest

from web.queries.energy import (
    SYNTHETIC_CURVE_PLATEAU_SOC,
    SYNTHETIC_CURVE_TAIL_FRACTION,
    build_synthetic_charge_curve_chart,
    synthesize_curve,
)

pytestmark = pytest.mark.unit


def test_phase_25_synthesize_curve_plateau_below_threshold():
    """synthesize_curve returns max_kw for every SOC <= plateau_soc."""
    points = synthesize_curve(max_kw=150.0)
    plateau_points = [p for p in points if p["soc"] <= SYNTHETIC_CURVE_PLATEAU_SOC]
    assert plateau_points
    for p in plateau_points:
        assert p["kw"] == pytest.approx(150.0)


def test_phase_25_synthesize_curve_linear_taper_above_threshold():
    """synthesize_curve linearly interpolates from max_kw to max_kw*tail_fraction across plateau_soc..100."""
    points = synthesize_curve(max_kw=100.0)
    # At SOC = 90 (halfway between 80 and 100), kw should be halfway between
    # max_kw (100) and end_kw (20) = 60
    p90 = next(p for p in points if p["soc"] == 90)
    assert p90["kw"] == pytest.approx(60.0, rel=0.01)


def test_phase_25_synthesize_curve_endpoint_value():
    """At SOC=100 the returned kW equals max_kw * tail_fraction (default 0.20)."""
    points = synthesize_curve(max_kw=200.0)
    p100 = next(p for p in points if p["soc"] == 100)
    assert p100["kw"] == pytest.approx(200.0 * SYNTHETIC_CURVE_TAIL_FRACTION)


def test_phase_25_synthesize_curve_monotonic_non_increasing():
    """Curve never increases as SOC increases (plateau then decay)."""
    points = synthesize_curve(max_kw=150.0)
    kws = [p["kw"] for p in points]
    for a, b in zip(kws, kws[1:]):  # noqa: B905 — intentional sliding window, lengths differ by design
        assert b <= a + 1e-9


def test_phase_25_build_synthetic_charge_curve_chart_empty_data_returns_empty_string():
    """build_synthetic_charge_curve_chart returns '' when session_count == 0 or max_kw == 0."""
    assert build_synthetic_charge_curve_chart(max_kw=0, dc_session_count=0) == ""
    assert build_synthetic_charge_curve_chart(max_kw=150, dc_session_count=0) == ""
    assert build_synthetic_charge_curve_chart(max_kw=0, dc_session_count=5) == ""
