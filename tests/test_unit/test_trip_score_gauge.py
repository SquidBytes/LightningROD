"""Unit tests for the trip-row charge-regenerated gauge.

Renders web/templates/driving/sessions/partials/trip_row.html in isolation
via Jinja2 Environment to assert the radial-progress gauge replaces the
previous static-gradient bar in one neutral colour. 0 is converted to NULL
upstream at the adapter, so it is not exercised here.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

from web.tooltips import TOOLTIPS

pytestmark = pytest.mark.unit

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "web" / "templates"


def _render(charge_regenerated):
    """Render trip_row.html with a single trip whose charge_regenerated is set.

    Other trip fields are None or neutral so this test only exercises the
    score-gauge cell. Filters cvt_dist / cvt_temp / cvt_eff are stubbed as
    pass-through identities — they are exercised by other tests.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["cvt_dist"] = lambda v, u: v
    env.filters["cvt_temp"] = lambda v, u: v
    env.filters["cvt_eff"] = lambda v, u: v
    env.filters["localtime"] = lambda v, tz, fmt="%Y-%m-%d": str(v)
    env.globals["tooltips"] = TOOLTIPS
    tpl = env.get_template("driving/sessions/partials/trip_row.html")
    trip = SimpleNamespace(
        id=1,
        charge_regenerated=charge_regenerated,
        end_time=None,
        distance=None,
        duration=None,
        outside_air_temp=None,
        cabin_temp=None,
        ambient_temp=None,
        energy_consumed=None,
        efficiency=None,
        range_regenerated=None,
    )
    return tpl.render(
        trip=trip,
        total=1,
        page=1,
        per_page=25,
        row_index=0,
        units={"distance_label": "mi", "range_label": "mi", "temp_label": "°F", "efficiency_label": "mi/kWh"},
        distance_unit="us",
        temp_unit="us",
        user_tz="UTC",
    )


@pytest.mark.parametrize("value", [3, 49, 50, 74, 75, 100])
def test_gauge_is_one_neutral_colour(value):
    """A regeneration percentage is not good or bad, so no value is painted red, amber or green."""
    html = _render(value)
    assert "radial-progress text-primary" in html
    for judged in ("text-error", "text-warning", "text-success"):
        assert judged not in html


def test_score_none_renders_emdash():
    """charge_regenerated=None → em-dash, no gauge rendered."""
    html = _render(None)
    assert "—" in html  # U+2014 em-dash
    assert "radial-progress" not in html


def test_score_value_present_in_gauge():
    """82 → gauge has --value:82 and centered text "82 %"."""
    html = _render(82)
    assert "--value:82" in html
    assert ">82 %<" in html


def test_static_gradient_removed():
    """The old red→green gradient bar must be gone."""
    html = _render(50)
    assert "bg-gradient-to-r from-error via-warning to-success" not in html


def test_aria_label_present():
    """Gauge names the metric and its unit for screen readers."""
    html = _render(60)
    assert 'aria-label="Charge regenerated: 60 %"' in html


def _render_with_duration(duration):
    """Render trip_row.html with only the duration field populated."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["cvt_dist"] = lambda v, u: v
    env.filters["cvt_temp"] = lambda v, u: v
    env.filters["cvt_eff"] = lambda v, u: v
    env.filters["localtime"] = lambda v, tz, fmt="%Y-%m-%d": str(v)
    env.globals["tooltips"] = TOOLTIPS
    tpl = env.get_template("driving/sessions/partials/trip_row.html")
    trip = SimpleNamespace(
        id=1,
        charge_regenerated=None,
        end_time=None,
        distance=None,
        duration=duration,
        outside_air_temp=None,
        cabin_temp=None,
        ambient_temp=None,
        energy_consumed=None,
        efficiency=None,
        range_regenerated=None,
    )
    return tpl.render(
        trip=trip,
        total=1,
        page=1,
        per_page=25,
        row_index=0,
        units={"distance_label": "mi", "range_label": "mi", "temp_label": "°F", "efficiency_label": "mi/kWh"},
        distance_unit="us",
        temp_unit="us",
        user_tz="UTC",
    )


def test_row_duration_uses_seconds_not_minutes():
    """Regression-lock: 3600s must render '1h 0m' in the desktop row, not '60h 0m'."""
    html = _render_with_duration(3600)
    assert "1h 0m" in html
    assert "60h" not in html


def test_row_duration_under_an_hour():
    """45 minutes (2700 seconds) → '45m', no hour token."""
    html = _render_with_duration(2700)
    assert "45m" in html
    assert "0h" not in html
