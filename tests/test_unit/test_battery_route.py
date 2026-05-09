"""Battery route + charge curve chart builder unit tests.

Covers:
- Charge curve picker default-latest selection.
- battery_temp summary value populated via a separate query that does NOT
  inherit the hv_battery_capacity filter.
- AC charge curve y-axis cap at 25 kW with reference curve hidden.
- DC charge curve y-axis cap at 200 kW with reference curve preserved.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.vehicle import EVVehicle
from web.queries.battery import (
    build_charge_curve_chart,
    build_metric_sparkline,
    query_battery_telemetry,
)
from web.queries.settings import set_app_setting

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_vehicle(db, device_id="ROUTE_VIN", make_active=True):
    v = EVVehicle(
        device_id=device_id,
        display_name="Route Test Vehicle",
        vin=device_id,
        source_system="test",
    )
    db.add(v)
    await db.flush()
    if make_active:
        await set_app_setting(db, "active_vehicle_id", str(v.id))
    return v


def _curve_data(charge_type=None):
    """Build minimal charge curve dict shape for build_charge_curve_chart."""
    session = SimpleNamespace(
        id=1,
        charge_type=charge_type,
        start_soc=20.0,
        end_soc=80.0,
        charging_kw=50.0,
        max_power=75.0,
    )
    detailed = [
        {"soc": 10.0, "kw": -50.0, "temp": 22.0, "timestamp": None},
        {"soc": 30.0, "kw": -80.0, "temp": 23.0, "timestamp": None},
        {"soc": 60.0, "kw": -120.0, "temp": 25.0, "timestamp": None},
        {"soc": 90.0, "kw": -30.0, "temp": 26.0, "timestamp": None},
    ]
    fallback = {
        "start_soc": 20.0,
        "end_soc": 80.0,
        "charging_kw": 50.0,
        "max_power": 75.0,
    }
    return {"detailed": detailed, "fallback": fallback, "session": session}


def _ref_curve():
    return [
        {"soc": 0, "kw": 50},
        {"soc": 50, "kw": 100},
        {"soc": 100, "kw": 0},
    ]


# ---------------------------------------------------------------------------
# Charge curve chart-builder tests (no DB needed)
# ---------------------------------------------------------------------------


def test_ac_charge_curve_y_cap():
    """AC charge_type caps the y-axis at 25 kW."""
    html = build_charge_curve_chart(_curve_data(charge_type="AC"), charge_type="AC")
    assert html
    # Plotly serialises range as a list; substring-match is robust to spacing.
    assert '"range":[0,25' in html or '"range": [0, 25' in html


def test_ac_charge_curve_hides_ref():
    """AC branch suppresses the reference curve overlay."""
    html = build_charge_curve_chart(
        _curve_data(charge_type="AC"),
        ref_curve=_ref_curve(),
        charge_type="AC",
    )
    assert html
    # The reference trace registers with name="Reference"; absence proves the
    # AC branch dropped it before fig.add_trace ran.
    assert '"name":"Reference"' not in html
    assert '"name": "Reference"' not in html


def test_dc_charge_curve_keeps_200_and_ref():
    """DC charge_type keeps the 200 kW cap and renders the reference curve."""
    html = build_charge_curve_chart(
        _curve_data(charge_type="DC"),
        ref_curve=_ref_curve(),
        charge_type="DC",
    )
    assert html
    assert '"range":[0,200' in html or '"range": [0, 200' in html
    assert '"name":"Reference"' in html or '"name": "Reference"' in html


def test_default_charge_type_keeps_dc_behaviour():
    """charge_type=None falls through to the DC default (200 kW + ref curve)."""
    html = build_charge_curve_chart(
        _curve_data(charge_type=None),
        ref_curve=_ref_curve(),
    )
    assert html
    assert '"range":[0,200' in html or '"range": [0, 200' in html


# ---------------------------------------------------------------------------
# Route-handler tests (DB-backed)
# ---------------------------------------------------------------------------


async def test_battery_temp_summary_populated_without_capacity_row(db_session):
    """Pitfall 7 regression: battery_temp populates from a row whose
    hv_battery_capacity is NULL (capacity-filter must NOT be inherited)."""
    from web.routes.battery import battery as battery_handler

    db = db_session
    await _seed_vehicle(db, device_id="TEMP_VIN")

    now = datetime.now(UTC)
    # Row with temperature but NO capacity — would be excluded by latest_stmt.
    db.add(
        EVBatteryStatus(
            device_id="TEMP_VIN",
            recorded_at=now,
            hv_battery_temperature=22.5,
            hv_battery_capacity=None,
            source_system="test",
        )
    )
    await db.flush()

    # Call handler directly so we can assert against the template context.
    captured = {}

    class _StubTemplates:
        def TemplateResponse(self, request, name, context):  # noqa: N802
            captured["context"] = context
            captured["template"] = name
            return None

    from web.routes import battery as route_module
    real_templates = route_module.templates
    route_module.templates = cast(Any, _StubTemplates())
    try:
        await battery_handler(
            request=cast(Any, None),
            db=db,
            range="7d",
            session=None,
            section=None,
            hx_request=None,
        )
    finally:
        route_module.templates = real_templates

    assert "context" in captured, "handler did not render a template"
    summary = captured["context"]["summary"]
    assert summary["battery_temp"] == pytest.approx(22.5)


async def test_default_session_is_latest(db_session):
    """When no ?session= is set and recent_sessions is non-empty, the handler
    pre-selects the most recent session as active_session."""
    from web.routes import battery as route_module
    from web.routes.battery import battery as battery_handler

    db = db_session
    await _seed_vehicle(db, device_id="LATEST_VIN")

    base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    older = EVChargingSession(
        device_id="LATEST_VIN",
        session_start_utc=base,
        energy_kwh=10.0,
        is_complete=True,
        source_system="test",
    )
    newer = EVChargingSession(
        device_id="LATEST_VIN",
        session_start_utc=base + timedelta(days=2),
        energy_kwh=20.0,
        is_complete=True,
        source_system="test",
    )
    db.add_all([older, newer])
    await db.flush()

    captured = {}

    class _StubTemplates:
        def TemplateResponse(self, request, name, context):  # noqa: N802
            captured["context"] = context
            return None

    real_templates = route_module.templates
    route_module.templates = cast(Any, _StubTemplates())
    try:
        await battery_handler(
            request=cast(Any, None), db=db, range="7d",
            session=None, section=None, hx_request=None,
        )
    finally:
        route_module.templates = real_templates

    assert captured["context"]["active_session"] == newer.id


async def test_explicit_session_overrides_default(db_session):
    """An explicit ?session= value is preserved; default-latest does not run."""
    from web.routes import battery as route_module
    from web.routes.battery import battery as battery_handler

    db = db_session
    await _seed_vehicle(db, device_id="EXPLICIT_VIN")

    base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    s1 = EVChargingSession(
        device_id="EXPLICIT_VIN",
        session_start_utc=base,
        energy_kwh=10.0,
        is_complete=True,
        source_system="test",
    )
    s2 = EVChargingSession(
        device_id="EXPLICIT_VIN",
        session_start_utc=base + timedelta(days=1),
        energy_kwh=20.0,
        is_complete=True,
        source_system="test",
    )
    db.add_all([s1, s2])
    await db.flush()

    captured = {}

    class _StubTemplates:
        def TemplateResponse(self, request, name, context):  # noqa: N802
            captured["context"] = context
            return None

    real_templates = route_module.templates
    route_module.templates = cast(Any, _StubTemplates())
    try:
        await battery_handler(
            request=cast(Any, None), db=db, range="7d",
            session=s1.id, section=None, hx_request=None,
        )
    finally:
        route_module.templates = real_templates

    assert captured["context"]["active_session"] == s1.id


async def test_no_sessions_means_no_default(db_session):
    """When there are no charging sessions, active_session stays None."""
    from web.routes import battery as route_module
    from web.routes.battery import battery as battery_handler

    db = db_session
    await _seed_vehicle(db, device_id="EMPTY_VIN")

    captured = {}

    class _StubTemplates:
        def TemplateResponse(self, request, name, context):  # noqa: N802
            captured["context"] = context
            return None

    real_templates = route_module.templates
    route_module.templates = cast(Any, _StubTemplates())
    try:
        await battery_handler(
            request=cast(Any, None), db=db, range="7d",
            session=None, section=None, hx_request=None,
        )
    finally:
        route_module.templates = real_templates

    assert captured["context"]["active_session"] is None


# ---------------------------------------------------------------------------
# HV Pack Telemetry mega-card
# ---------------------------------------------------------------------------


def test_metric_sparkline_returns_html_for_multi_point_series():
    series = [
        {"recorded_at": datetime(2026, 5, 1, 10, 0, tzinfo=UTC), "value": 410.0},
        {"recorded_at": datetime(2026, 5, 2, 10, 0, tzinfo=UTC), "value": 415.0},
        {"recorded_at": datetime(2026, 5, 3, 10, 0, tzinfo=UTC), "value": 405.0},
    ]
    html = build_metric_sparkline(series, color="#a78bfa")
    assert html is not None
    assert "plotly-chart-wrap" in html


def test_metric_sparkline_skips_thin_series():
    """A single-point series can't draw a line — builder returns None."""
    one_point = [{"recorded_at": datetime(2026, 5, 1, tzinfo=UTC), "value": 410.0}]
    assert build_metric_sparkline(one_point) is None
    assert build_metric_sparkline([]) is None


def test_metric_sparkline_applies_transform():
    """The transform callable is applied to every value before plotting."""
    series = [
        {"recorded_at": datetime(2026, 5, 1, tzinfo=UTC), "value": 0.0},
        {"recorded_at": datetime(2026, 5, 2, tzinfo=UTC), "value": 100.0},
    ]
    html = build_metric_sparkline(series, transform=lambda c: c * 2)
    assert html
    # Plotly serializes y-values into the JSON payload — the doubled values
    # confirm the transform was applied (raw 100 would not appear; 200 should).
    assert "200" in html


async def test_query_battery_telemetry_returns_latest_and_series(db_session):
    """The telemetry query fans one DB scan into latest + series for each field."""
    db = db_session
    await _seed_vehicle(db, device_id="TELEMETRY_VIN")

    now = datetime.now(UTC)
    db.add_all([
        EVBatteryStatus(
            device_id="TELEMETRY_VIN",
            recorded_at=now - timedelta(hours=2),
            hv_battery_temperature=20.0,
            hv_battery_voltage=410.0,
            hv_battery_amperage=-2.0,
            hv_battery_kw=-0.8,
            source_system="test",
        ),
        EVBatteryStatus(
            device_id="TELEMETRY_VIN",
            recorded_at=now - timedelta(hours=1),
            hv_battery_temperature=22.5,
            hv_battery_voltage=415.0,
            hv_battery_amperage=-1.5,
            hv_battery_kw=-0.6,
            source_system="test",
        ),
    ])
    await db.flush()

    out = await query_battery_telemetry(db, device_id="TELEMETRY_VIN", days=7)

    assert out["latest"]["hv_battery_voltage"]["value"] == pytest.approx(415.0)
    assert out["latest"]["hv_battery_temperature"]["value"] == pytest.approx(22.5)
    assert out["latest"]["hv_battery_amperage"]["value"] == pytest.approx(-1.5)
    assert out["latest"]["hv_battery_kw"]["value"] == pytest.approx(-0.6)
    # Each series carries both readings in chronological order.
    assert len(out["series"]["hv_battery_voltage"]) == 2
    assert out["series"]["hv_battery_voltage"][0]["value"] == pytest.approx(410.0)


async def test_query_battery_telemetry_excludes_stale_rows(db_session):
    """Rows older than `days` must not contribute to latest or series."""
    db = db_session
    await _seed_vehicle(db, device_id="STALE_VIN")

    now = datetime.now(UTC)
    # Old row (well outside the 7d window).
    db.add(
        EVBatteryStatus(
            device_id="STALE_VIN",
            recorded_at=now - timedelta(days=30),
            hv_battery_voltage=999.0,
            source_system="test",
        )
    )
    # Fresh row.
    db.add(
        EVBatteryStatus(
            device_id="STALE_VIN",
            recorded_at=now,
            hv_battery_voltage=412.0,
            source_system="test",
        )
    )
    await db.flush()

    out = await query_battery_telemetry(db, device_id="STALE_VIN", days=7)
    assert out["latest"]["hv_battery_voltage"]["value"] == pytest.approx(412.0)
    assert all(s["value"] != 999.0 for s in out["series"]["hv_battery_voltage"])


async def test_telemetry_context_carries_all_four_metrics(db_session):
    """Route context must expose telemetry with Temp/Voltage/Amperage/kW slots."""
    from web.routes import battery as route_module
    from web.routes.battery import battery as battery_handler

    db = db_session
    await _seed_vehicle(db, device_id="CTX_VIN")

    now = datetime.now(UTC)
    db.add(
        EVBatteryStatus(
            device_id="CTX_VIN",
            recorded_at=now,
            hv_battery_temperature=21.0,
            hv_battery_voltage=413.0,
            hv_battery_amperage=-1.2,
            hv_battery_kw=-0.5,
            source_system="test",
        )
    )
    await db.flush()

    captured = {}

    class _StubTemplates:
        def TemplateResponse(self, request, name, context):  # noqa: N802
            captured["context"] = context
            return None

    real_templates = route_module.templates
    route_module.templates = cast(Any, _StubTemplates())
    try:
        await battery_handler(
            request=cast(Any, None), db=db, range="7d",
            session=None, section=None, hx_request=None,
        )
    finally:
        route_module.templates = real_templates

    telemetry = captured["context"]["telemetry"]
    assert set(telemetry.keys()) == {
        "hv_battery_temperature",
        "hv_battery_voltage",
        "hv_battery_amperage",
        "hv_battery_kw",
    }
    # Voltage carries the freshest value and a recorded_at timestamp.
    v = telemetry["hv_battery_voltage"]
    assert v["value"] == pytest.approx(413.0)
    assert v["recorded_at"] is not None
    assert v["unit"] == "V"
