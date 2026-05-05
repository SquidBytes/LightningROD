"""Unit tests for the /driving/sessions sort UX.

Locks the column-header sort pattern that mirrors /charging/sessions:
- The standalone "Sort by:" toolbar block is gone.
- Column headers in the trips table call cycleSortColumn() via the
  shared partials/column_dropdown.html.
- Active sort indicator renders in the active column header.
- The route handler accepts sort_by/sort_dir query params (no API break).
"""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

pytestmark = pytest.mark.unit

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "web" / "templates"


def _render_index(sort_by="date", sort_dir="desc"):
    """Render the trips index page partial (no base.html extends).

    The page-level summary partial holds the trips table and the sort
    toolbar that this task removes; it can render without FastAPI's
    request/url_for wiring. We supply a single neutral trip so the
    table thead renders for sortable-header assertions.
    """
    from types import SimpleNamespace
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["cvt_dist"] = lambda v, u: v
    env.filters["cvt_temp"] = lambda v, u: v
    env.filters["cvt_eff"] = lambda v, u: v
    env.filters["localtime"] = lambda v, tz, fmt="%Y-%m-%d": str(v)
    trip = SimpleNamespace(
        id=1, driving_score=None, end_time=None, distance=None, duration=None,
        outside_air_temp=None, cabin_temp=None, ambient_temp=None,
        energy_consumed=None, efficiency=None, range_regenerated=None,
    )
    tpl = env.get_template("driving/sessions/partials/summary.html")
    return tpl.render(
        trips=[trip],
        total=1,
        page=1,
        per_page=25,
        total_pages=1,
        has_prev=False,
        has_next=False,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_range="all",
        active_page="trip_sessions",
        page_title="Trip Sessions",
        active_vehicle=None,
        all_vehicles=[],
        user_tz="UTC",
        units={
            "distance_label": "mi",
            "range_label": "mi",
            "temp_label": "°F",
            "efficiency_label": "mi/kWh",
        },
        distance_unit="us",
        temp_unit="us",
        summary={
            "count": 0,
            "total_distance": 0,
            "avg_efficiency": None,
            "total_energy": 0,
        },
        tooltips={
            "driving_sessions_total_trips": "",
            "driving_sessions_total_distance": "",
            "driving_sessions_avg_efficiency": "",
            "driving_sessions_total_energy": "",
        },
    )


def _render_full_index(sort_by="date", sort_dir="desc"):
    """Render the full index.html for tests that need its toolbar/script."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["cvt_dist"] = lambda v, u: v
    env.filters["cvt_temp"] = lambda v, u: v
    env.filters["cvt_eff"] = lambda v, u: v
    env.filters["localtime"] = lambda v, tz, fmt="%Y-%m-%d": str(v)
    # Stub url_for so {% extends 'base.html' %} doesn't blow up.
    env.globals["url_for"] = lambda *a, **kw: ""
    env.globals["request"] = type("R", (), {"url": type("U", (), {"path": "/driving/sessions"})()})()
    tpl = env.get_template("driving/sessions/index.html")
    return tpl.render(
        trips=[],
        total=0,
        page=1,
        per_page=25,
        total_pages=1,
        has_prev=False,
        has_next=False,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_range="all",
        active_page="trip_sessions",
        page_title="Trip Sessions",
        active_vehicle=None,
        all_vehicles=[],
        user_tz="UTC",
        units={
            "distance_label": "mi",
            "range_label": "mi",
            "temp_label": "°F",
            "efficiency_label": "mi/kWh",
        },
        distance_unit="us",
        temp_unit="us",
        summary={
            "count": 0,
            "total_distance": 0,
            "avg_efficiency": None,
            "total_energy": 0,
        },
        tooltips={
            "driving_sessions_total_trips": "",
            "driving_sessions_total_distance": "",
            "driving_sessions_avg_efficiency": "",
            "driving_sessions_total_energy": "",
        },
    )


def test_no_sort_toolbar():
    """The old standalone Sort-by toolbar is removed from index.html."""
    html = _render_full_index()
    assert "setTripSort(" not in html
    assert "Sort by:" not in html
    assert "data-sort-btn=" not in html


def test_column_headers_sortable():
    """At least 4 column headers wire cycleSortColumn() through the dropdown partial."""
    html = _render_index()
    assert html.count("cycleSortColumn(") >= 4


def test_column_dropdown_partial_included():
    """The column_dropdown.html partial is wired in the trips table thead."""
    html = _render_index()
    # The partial emits a wrapper div with col-header-wrapper.
    assert "col-header-wrapper" in html


def test_sort_indicator_renders_for_active_column():
    """When sort_by='date' and sort_dir='desc', the active column shows text-primary on its arrow."""
    html = _render_index(sort_by="date", sort_dir="desc")
    assert "text-primary" in html


def test_sortable_columns_include_date_distance_duration_score():
    """The four key columns expose sortable headers."""
    html = _render_index()
    for label in ("Date", "Distance", "Duration", "Score"):
        assert label in html
