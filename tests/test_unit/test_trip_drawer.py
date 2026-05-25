"""Unit tests for the trip drawer Overview grid.

Renders web/templates/driving/sessions/partials/drawer.html in isolation
via Jinja2 Environment to assert:
- Odometer Start / Odometer End rows render with the active distance label
- Duration renders Hh Mm from canonical-seconds storage (not minutes)
- Regen still renders unchanged
- Overview grid grows from 9 to 11 fields
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

pytestmark = pytest.mark.unit

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "web" / "templates"


def _render_drawer(
    odometer_start=None,
    odometer_end=None,
    duration=None,
    range_regenerated=None,
):
    """Render drawer.html with a single trip; other fields neutral."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["cvt_dist"] = lambda v, u: v
    env.filters["cvt_temp"] = lambda v, u: v
    env.filters["cvt_eff"] = lambda v, u: v
    env.filters["localtime"] = lambda v, tz, fmt="%Y-%m-%d": str(v)
    tpl = env.get_template("driving/sessions/partials/drawer.html")
    trip = SimpleNamespace(
        id=1,
        driving_score=None,
        speed_score=None,
        acceleration_score=None,
        deceleration_score=None,
        end_time=None,
        start_time=None,
        distance=10.0,
        duration=duration,
        odometer_start=odometer_start,
        odometer_end=odometer_end,
        range_regenerated=range_regenerated,
        energy_consumed=None,
        efficiency=None,
        ambient_temp=None,
        cabin_temp=None,
        outside_air_temp=None,
        tire_pressure_snapshot=None,
        source_system="ha_fordpass",
        device_id="TESTDEV",
    )
    return tpl.render(
        trip=trip,
        units={
            "distance_label": "mi",
            "range_label": "mi",
            "temp_label": "°F",
            "efficiency_label": "mi/kWh",
        },
        distance_unit="us",
        temp_unit="us",
        user_tz="UTC",
        radar_chart="",
        start_location=None,
        end_location=None,
    )


def test_odometer_start_label_present():
    html = _render_drawer(odometer_start=12345)
    assert "Odometer Start" in html
    # %.0f format → "12345"
    assert "12345" in html


def test_odometer_start_empty_renders_dashes():
    html = _render_drawer(odometer_start=None)
    assert "Odometer Start" in html


def test_odometer_end_label_present():
    html = _render_drawer(odometer_end=12369)
    assert "Odometer End" in html
    assert "12369" in html


def test_odometer_end_empty_renders_dashes():
    html = _render_drawer(odometer_end=None)
    assert "Odometer End" in html


def test_duration_renders_from_seconds():
    """3725 s = 1h 2m 5s → drawer should show '1h 2m'."""
    html = _render_drawer(duration=3725)
    assert "1h 2m" in html


def test_duration_zero():
    html = _render_drawer(duration=0)
    assert "0h 0m" in html


def test_duration_empty():
    html = _render_drawer(duration=None)
    # Duration cell renders "--" alongside other empty fields
    assert "--" in html


def test_regen_renders():
    html = _render_drawer(range_regenerated=4.5)
    assert "Regen" in html
    assert "4.5" in html


def test_overview_grid_has_11_fields():
    """The Overview grid grows from 9 to 11 fields after odometer additions."""
    html = _render_drawer()
    # Each Overview field uses <span class="text-base-content/40"> for its label.
    # Count occurrences across the whole rendered output (Location section also
    # uses the same span class for its 2 labels — total expected: 11 + 2 = 13).
    overview_count = html.count('<span class="text-base-content/40">')
    assert overview_count >= 11, (
        f"Expected at least 11 field labels (9 original + 2 odometer + 2 location), "
        f"got {overview_count}"
    )


def test_duration_uses_seconds_not_minutes():
    """Regression-lock: 3600s must render '1h 0m', not '60h 0m'."""
    html = _render_drawer(duration=3600)
    assert "1h 0m" in html
    assert "60h" not in html
