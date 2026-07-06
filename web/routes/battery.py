"""Battery analytics page and HTMX chart endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from web.dependencies import get_db
from web.queries.battery import (
    build_battery_temp_chart,
    build_charge_curve_chart,
    build_degradation_chart,
    build_metric_sparkline,
    build_soc_timeline_chart,
    detect_charging_regions,
    load_reference_charge_curve,
    query_average_charge_curve,
    query_battery_telemetry,
    query_battery_temp_timeline,
    query_charge_curve,
    query_degradation_by_mileage,
    query_outside_temp_timeline,
    query_recent_sessions_for_picker,
    query_soc_timeline,
)
from web.queries.settings import get_app_setting, get_unit_context
from web.queries.vehicles import (
    get_active_device_id,
    get_active_vehicle,
    get_all_vehicles,
)
from web.unit_system import MI_PER_KM

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/battery", response_class=HTMLResponse)
async def battery(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: str | None = "7d",
    date_from: str | None = None,
    date_to: str | None = None,
    session: int | None = None,
    section: str | None = None,
    hx_request: Annotated[str | None, Header()] = None,
):
    # A custom date window suppresses the preset fallback so the window applies.
    time_range = range or ("all" if (date_from or date_to) else "7d")

    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    unit_ctx = await get_unit_context(db)
    distance_factor = MI_PER_KM if unit_ctx["distance_unit"] == "us" else 1.0
    user_tz = await get_app_setting(db, "user_timezone", "UTC")

    # Rated capacity for health/degradation math. FordPass reports the GROSS
    # pack capacity via `maximumBatteryCapacity` (stored in hv_battery_capacity),
    # so we must compare against the gross rating, not usable. Falls back to
    # the usable value if gross is unset, then to a 75 kWh default.
    rated_capacity = 75.0
    if active_vehicle:
        if active_vehicle.battery_gross_capacity_kwh:
            rated_capacity = float(active_vehicle.battery_gross_capacity_kwh)
        elif active_vehicle.battery_capacity_kwh:
            rated_capacity = float(active_vehicle.battery_capacity_kwh)

    # Section-specific partial rendering for lazy loading
    if section == "degradation":
        degradation_data = await query_degradation_by_mileage(
            db, time_range=time_range, device_id=active_device_id,
            date_from=date_from, date_to=date_to,
        )
        chart = build_degradation_chart(
            degradation_data,
            rated_capacity,
            distance_factor=distance_factor,
            distance_label=unit_ctx["units"]["distance_label"],
            user_tz=user_tz,
        )
        if chart:
            return HTMLResponse(chart)
        return HTMLResponse('<p class="text-base-content/40 text-sm py-8 text-center">No capacity or odometer data available.</p>')

    if section == "charge_curve":
        ref_curve_data = load_reference_charge_curve(active_vehicle)
        ref_curve = ref_curve_data["curve"] if ref_curve_data else None
        avg_curve = await query_average_charge_curve(db, device_id=active_device_id)
        if session:
            curve_data = await query_charge_curve(db, session_id=session)
            sess_obj = curve_data.get("session") if isinstance(curve_data, dict) else None
            charge_type = getattr(sess_obj, "charge_type", None) if sess_obj is not None else None
            chart = build_charge_curve_chart(
                curve_data,
                ref_curve=ref_curve,
                avg_curve=avg_curve,
                charge_type=charge_type,
            )
            if chart:
                return HTMLResponse(chart)
        return HTMLResponse('<p class="text-base-content/40 text-sm py-8 text-center">No charging sessions in this time range.</p>')

    if section == "battery_temp":
        temp_data = await query_battery_temp_timeline(
            db, time_range=time_range, device_id=active_device_id,
            date_from=date_from, date_to=date_to,
        )
        outside_data = await query_outside_temp_timeline(
            db, time_range=time_range, device_id=active_device_id,
            date_from=date_from, date_to=date_to,
        )
        # Reuse the SOC charging regions for the overlay. detect_charging_regions
        # returns (start_idx, end_idx) tuples against soc_data_for_regions; the
        # chart builder needs timestamp tuples so x-coords align with the temp
        # series (independent cadences).
        soc_data_for_regions = await query_soc_timeline(
            db, time_range=time_range, device_id=active_device_id,
            date_from=date_from, date_to=date_to,
        )
        charging_regions_idx = detect_charging_regions(soc_data_for_regions)
        n = len(soc_data_for_regions)
        charging_regions_ts = [
            (
                soc_data_for_regions[s]["recorded_at"],
                soc_data_for_regions[e]["recorded_at"],
            )
            for s, e in charging_regions_idx
            if s < n and e < n
        ]
        temp_chart = build_battery_temp_chart(
            temp_data,
            outside_data,
            charging_regions_ts,
            temp_factor_f=(unit_ctx["temp_unit"] == "us"),
            temp_label=unit_ctx["units"]["temp_label"],
        )
        if temp_chart:
            return HTMLResponse(temp_chart)
        return HTMLResponse(
            '<p class="text-base-content/40 text-sm py-8 text-center">'
            'No temperature data available for this time range.</p>'
        )

    # Full page or HTMX filter change: compute only SOC timeline + summary cards
    all_vehicles = await get_all_vehicles(db)

    # Load reference charge curve name for display
    ref_curve_data = load_reference_charge_curve(active_vehicle)

    # 1. SOC timeline
    soc_data = await query_soc_timeline(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )
    charging_regions = detect_charging_regions(soc_data)
    soc_chart = build_soc_timeline_chart(
        soc_data,
        charging_regions,
        distance_factor=distance_factor,
        range_label=unit_ctx["units"]["range_label"],
        user_tz=user_tz,
    )

    # Build session time windows for click-to-drill JS
    session_time_windows = []
    recent_sessions = await query_recent_sessions_for_picker(db, device_id=active_device_id)
    for s in recent_sessions:
        if s.get("session_start_utc"):
            session_time_windows.append({
                "id": s["id"],
                "start": s["session_start_utc"].isoformat() if hasattr(s["session_start_utc"], "isoformat") else str(s["session_start_utc"]),
            })

    active_session = session
    if not active_session and recent_sessions:
        # Default to the most recent session of any charge_type when no
        # ?session= override is present. recent_sessions is ordered by
        # session_start_utc DESC inside query_recent_sessions_for_picker.
        active_session = recent_sessions[0]["id"]

    # Summary card values (health-focused)
    summary = {
        "health_pct": None,
        "current_capacity": None,
        "rated_capacity": rated_capacity,
        "capacity_delta": None,
        "rated_range": None,
        "latest_range": None,
        "range_delta": None,
        "lv_voltage": None,
        "lv_level": None,
        "battery_temp": None,
    }

    # Latest battery status for summary
    latest_stmt = (
        select(
            EVBatteryStatus.hv_battery_capacity,
            EVBatteryStatus.hv_battery_range,
            EVBatteryStatus.hv_battery_max_range,
            EVBatteryStatus.lv_battery_voltage,
            EVBatteryStatus.lv_battery_level,
        )
        .where(EVBatteryStatus.hv_battery_capacity.isnot(None))
        .order_by(EVBatteryStatus.recorded_at.desc())
        .limit(1)
    )
    if active_device_id:
        latest_stmt = latest_stmt.where(EVBatteryStatus.device_id == active_device_id)
    latest_result = await db.execute(latest_stmt)
    latest = latest_result.first()
    if latest:
        cap = float(latest.hv_battery_capacity)
        summary["current_capacity"] = cap
        summary["health_pct"] = (cap / rated_capacity) * 100
        summary["capacity_delta"] = cap - rated_capacity
        if latest.hv_battery_range is not None:
            summary["latest_range"] = float(latest.hv_battery_range) * distance_factor
        if latest.hv_battery_max_range is not None:
            summary["rated_range"] = float(latest.hv_battery_max_range) * distance_factor
        if summary["latest_range"] is not None and summary["rated_range"] is not None:
            summary["range_delta"] = summary["latest_range"] - summary["rated_range"]
        if latest.lv_battery_voltage is not None:
            summary["lv_voltage"] = float(latest.lv_battery_voltage)
        if latest.lv_battery_level is not None:
            summary["lv_level"] = float(latest.lv_battery_level)

    # Fallback: separate query for 12v data if main query didn't have it
    if summary["lv_voltage"] is None:
        lv_stmt = (
            select(EVBatteryStatus.lv_battery_voltage, EVBatteryStatus.lv_battery_level)
            .where(EVBatteryStatus.lv_battery_voltage.isnot(None))
            .order_by(EVBatteryStatus.recorded_at.desc())
            .limit(1)
        )
        if active_device_id:
            lv_stmt = lv_stmt.where(EVBatteryStatus.device_id == active_device_id)
        lv_result = await db.execute(lv_stmt)
        lv_latest = lv_result.first()
        if lv_latest:
            summary["lv_voltage"] = float(lv_latest.lv_battery_voltage)
            summary["lv_level"] = float(lv_latest.lv_battery_level) if lv_latest.lv_battery_level else None

    # HV-pack telemetry block (Temp / Voltage / Amperage / kW) drives the
    # headline mega-card. One query fans out into latest values + 7d sparklines
    # for each metric.
    telemetry_raw = await query_battery_telemetry(db, device_id=active_device_id, days=7)
    telemetry_latest = telemetry_raw["latest"]
    series = telemetry_raw["series"]

    # Latest battery_temp also feeds back into `summary` for any legacy reader.
    temp_latest = telemetry_latest.get("hv_battery_temperature")
    if temp_latest is not None:
        summary["battery_temp"] = temp_latest["value"]

    # Convert pack-temp sparkline values into the user's display unit so the
    # mini-line tracks the headline value (which is converted in-template).
    def _c_to_user_unit(c: float) -> float:
        return (c * 9.0 / 5.0) + 32.0 if unit_ctx["temp_unit"] == "us" else c

    def _val(field):
        latest = telemetry_latest.get(field)
        return latest["value"] if latest else None

    def _ts(field):
        latest = telemetry_latest.get(field)
        return latest["recorded_at"] if latest else None

    telemetry = {
        "hv_battery_temperature": {
            "label": "Pack Temp",
            "value": _c_to_user_unit(_val("hv_battery_temperature")) if _val("hv_battery_temperature") is not None else None,
            "unit": unit_ctx["units"]["temp_label"],
            "recorded_at": _ts("hv_battery_temperature"),
            "sparkline": build_metric_sparkline(
                series["hv_battery_temperature"],
                color="#fbbf24",
                transform=_c_to_user_unit,
                zero_line=True,
            ),
            "fmt": "{:.0f}",
        },
        "hv_battery_voltage": {
            "label": "Voltage",
            "value": _val("hv_battery_voltage"),
            "unit": "V",
            "recorded_at": _ts("hv_battery_voltage"),
            "sparkline": build_metric_sparkline(
                series["hv_battery_voltage"], color="#a78bfa",
            ),
            "fmt": "{:.0f}",
        },
        "hv_battery_amperage": {
            "label": "Amperage",
            "value": _val("hv_battery_amperage"),
            "unit": "A",
            "recorded_at": _ts("hv_battery_amperage"),
            "sparkline": build_metric_sparkline(
                series["hv_battery_amperage"], color="#34d399",
                zero_line=True,
            ),
            "fmt": "{:+.1f}",
        },
        "hv_battery_kw": {
            "label": "Power",
            "value": _val("hv_battery_kw"),
            "unit": "kW",
            "recorded_at": _ts("hv_battery_kw"),
            "sparkline": build_metric_sparkline(
                series["hv_battery_kw"], color="#47A8E5",
                zero_line=True,
            ),
            "fmt": "{:+.1f}",
        },
    }
    # All four metrics ingest from the same EVBatteryStatus row, so any one
    # populated recorded_at represents the whole card. Pick the freshest.
    telemetry_latest_at = max(
        (m["recorded_at"] for m in telemetry.values() if m["recorded_at"]),
        default=None,
    )

    # Degradation, charge curve, and 12v charts are NOT computed here --
    # they are lazy-loaded via HTMX hx-trigger="revealed"
    context = {
        **unit_ctx,
        "soc_chart": soc_chart,
        "degradation_chart": None,
        "charge_curve_chart": None,
        "ref_curve_name": ref_curve_data["name"] if ref_curve_data else None,
        "summary": summary,
        "telemetry": telemetry,
        "telemetry_latest_at": telemetry_latest_at,
        "sessions_list": recent_sessions,
        "session_time_windows": session_time_windows,
        "active_range": time_range,
        "date_from": date_from,
        "date_to": date_to,
        "active_session": active_session,
        "active_page": "battery",
        "page_title": "Battery Analytics",
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
        "user_tz": user_tz,
    }

    if hx_request:
        return templates.TemplateResponse(request, "battery/partials/summary.html", context)
    return templates.TemplateResponse(request, "battery/index.html", context)
