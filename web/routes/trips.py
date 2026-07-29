"""Driving-session list and trip-detail routes."""

import math
import uuid
from datetime import date
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.dependencies import get_db
from web.queries.settings import get_app_setting, get_unit_context
from web.queries.trips import (
    build_drive_graph,
    build_driving_score_radar,
    build_environment_chart,
    build_expanded_battery_chart,
    build_expanded_driving_chart,
    build_expanded_environment_chart,
    build_short_trip_filter,
    get_trip_hide_settings,
    query_hidden_trip_count,
    query_trip_battery_series,
    query_trip_vehicle_series,
    query_trips,
    resolve_trip_location_names,
)
from web.queries.vehicles import (
    get_active_device_id,
    get_active_vehicle,
    get_all_vehicles,
)
from web.services.sources.ha_fordpass.adapter import LIGHTNINGROD_TRIP_NAMESPACE
from web.unit_system import MI_PER_KM, parse_user_local_to_utc, to_metric_distance

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

PER_PAGE = 25


@router.get("/sessions", response_class=HTMLResponse)
async def trips(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: str | None = "30d",
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
    page: int = 1,
    hx_request: Annotated[str | None, Header()] = None,
):
    # A custom date window suppresses the preset fallback so the window applies.
    time_range = range or ("all" if (date_from or date_to) else "30d")
    # Accept the new sort_by/sort_dir form fields; fall back to the legacy
    # sort/dir aliases so deep-linked URLs from before the column-header
    # switch keep working.
    sort_by = sort_by or sort or "date"
    sort_dir = sort_dir or dir or "desc"

    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    unit_ctx = await get_unit_context(db)
    distance_factor = MI_PER_KM if unit_ctx["distance_unit"] == "us" else 1.0

    # Query trips and efficiency trend (metric base)
    hide = await get_trip_hide_settings(db)
    hide_filter = build_short_trip_filter(hide)
    trip_list, total, summary = await query_trips(
        db=db,
        page=page,
        per_page=PER_PAGE,
        date_preset=time_range,
        sort_by=sort_by,
        sort_dir=sort_dir,
        device_id=active_device_id,
        date_from=date_from,
        date_to=date_to,
        hide_filter=hide_filter,
    )

    hidden_count = 0
    if hide_filter is not None:
        hidden_count = await query_hidden_trip_count(
            db, hide, date_preset=time_range, device_id=active_device_id,
            date_from=date_from, date_to=date_to,
        )

    # Convert summary totals to display units
    if summary.get("total_distance") is not None:
        summary["total_distance"] = summary["total_distance"] * distance_factor
    if summary.get("avg_efficiency") is not None:
        summary["avg_efficiency"] = summary["avg_efficiency"] * distance_factor

    # Pagination
    total_pages = max(math.ceil(total / PER_PAGE), 1)
    has_prev = page > 1
    has_next = page < total_pages

    context = {
        **unit_ctx,
        "trips": trip_list,
        "total": total,
        "summary": summary,
        "hidden_count": hidden_count,
        "active_range": time_range,
        "date_from": date_from,
        "date_to": date_to,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "page": page,
        "total_pages": total_pages,
        "has_prev": has_prev,
        "has_next": has_next,
        "per_page": PER_PAGE,
        "active_page": "trip_sessions",
        "page_title": "Trip Sessions",
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
        "user_tz": user_tz,
    }

    if hx_request:
        return templates.TemplateResponse(request, "driving/sessions/partials/summary.html", context)
    return templates.TemplateResponse(request, "driving/sessions/index.html", context)


@router.get("/sessions/export.csv")
async def export_trips_csv(
    db: AsyncSession = Depends(get_db),
    range: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
):
    """Download the trip list as CSV, honoring the active filters.

    Distances export in the configured display unit; temperatures in the
    configured temp unit; times in the user's timezone; energy in kWh.
    """
    import csv
    import io
    from zoneinfo import ZoneInfo

    from web.unit_system import convert_distance, convert_efficiency, convert_temp

    # Resolve filters identically to the list route.
    time_range = range or ("all" if (date_from or date_to) else "30d")
    sort_by = sort_by or sort or "date"
    sort_dir = sort_dir or dir or "desc"

    active_device_id = await get_active_device_id(db)
    hide = await get_trip_hide_settings(db)
    hide_filter = build_short_trip_filter(hide)

    trip_list, _total, _summary = await query_trips(
        db=db,
        fetch_all=True,
        date_preset=time_range,
        sort_by=sort_by,
        sort_dir=sort_dir,
        device_id=active_device_id,
        date_from=date_from,
        date_to=date_to,
        hide_filter=hide_filter,
    )

    unit_ctx = await get_unit_context(db)
    distance_unit = unit_ctx["distance_unit"]
    temp_unit = unit_ctx["temp_unit"]
    distance_label = unit_ctx["units"]["distance_label"]
    eff_label = unit_ctx["units"]["efficiency_label"]
    temp_label = unit_ctx["units"]["temp_label"]
    user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
    try:
        tz = ZoneInfo(user_tz)
    except Exception:
        tz = ZoneInfo("UTC")

    def _local(dt) -> str:
        if dt is None:
            return ""
        if dt.tzinfo is None:
            from datetime import UTC as _utc
            dt = dt.replace(tzinfo=_utc)
        return dt.astimezone(tz).isoformat(sep=" ", timespec="minutes")

    def _num(v, dp: int) -> str:
        return "" if v is None else f"{float(v):.{dp}f}"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        f"start ({user_tz})", f"end ({user_tz})", "start_location", "end_location",
        f"distance_{distance_label}", "duration_min", "energy_consumed_kwh",
        f"efficiency_{eff_label}", f"odometer_start_{distance_label}",
        f"odometer_end_{distance_label}", f"ambient_temp_{temp_label}",
        f"cabin_temp_{temp_label}", f"outside_air_temp_{temp_label}",
        "driving_score", "speed_score", "acceleration_score", "deceleration_score",
        f"range_regenerated_{distance_label}", "source_system",
    ])
    for t in trip_list:
        start_location, end_location = await resolve_trip_location_names(db, t)
        # Prefer the per-trip distance/energy efficiency shown in the row, with
        # the stored efficiency as fallback — then convert to the display unit.
        if t.distance is not None and t.energy_consumed and t.energy_consumed > 0:
            eff = float(t.distance) / float(t.energy_consumed)
        else:
            eff = t.efficiency
        writer.writerow([
            _local(t.start_time),
            _local(t.end_time),
            start_location or "",
            end_location or "",
            _num(convert_distance(t.distance, distance_unit), 1),
            _num(float(t.duration) / 60 if t.duration is not None else None, 0),
            _num(t.energy_consumed, 2),
            _num(convert_efficiency(eff, distance_unit), 1),
            _num(convert_distance(t.odometer_start, distance_unit), 1),
            _num(convert_distance(t.odometer_end, distance_unit), 1),
            _num(convert_temp(t.ambient_temp, temp_unit), 1),
            _num(convert_temp(t.cabin_temp, temp_unit), 1),
            _num(convert_temp(t.outside_air_temp, temp_unit), 1),
            _num(t.driving_score, 0),
            _num(t.speed_score, 0),
            _num(t.acceleration_score, 0),
            _num(t.deceleration_score, 0),
            _num(convert_distance(t.range_regenerated, distance_unit), 1),
            t.source_system or "",
        ])

    filename = f"trip-sessions-{date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions/{trip_id}/detail", response_class=HTMLResponse)
async def trip_detail(
    request: Request,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.id == trip_id)
    )
    trip = result.scalar_one_or_none()

    if trip is None:
        return HTMLResponse(
            content="<p class='text-base-content/40 p-4'>Trip not found.</p>",
            status_code=404,
        )

    radar_chart = build_driving_score_radar(trip)
    unit_ctx = await get_unit_context(db)

    context = {
        **unit_ctx,
        "trip": trip,
        "radar_chart": radar_chart,
    }
    return templates.TemplateResponse(request, "driving/sessions/partials/trip_detail.html", context)


@router.get("/sessions/{trip_id}/drawer", response_class=HTMLResponse)
async def trip_drawer(
    request: Request,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.id == trip_id)
    )
    trip = result.scalar_one_or_none()

    if trip is None:
        return HTMLResponse(
            content="<p class='text-base-content/40 p-4'>Trip not found.</p>",
            status_code=404,
        )

    radar_chart = build_driving_score_radar(trip)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    unit_ctx = await get_unit_context(db)

    start_location, end_location = await resolve_trip_location_names(db, trip)

    context = {
        **unit_ctx,
        "trip": trip,
        "radar_chart": radar_chart,
        "start_location": start_location,
        "end_location": end_location,
        "user_tz": user_tz,
    }
    return templates.TemplateResponse(request, "driving/sessions/partials/drawer.html", context)


@router.get("/sessions/{trip_id}/charts/environment", response_class=HTMLResponse)
async def trip_environment_chart(
    request: Request,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip or not trip.start_time or not trip.end_time:
        return HTMLResponse(
            '<p class="text-base-content/30 text-sm py-4 text-center">No temperature data available for this trip.</p>'
        )
    vehicle_df = await query_trip_vehicle_series(db, trip.device_id, trip.start_time, trip.end_time)
    unit_ctx = await get_unit_context(db)
    chart_html = build_environment_chart(
        vehicle_df,
        temp_factor_f=(unit_ctx["temp_unit"] == "us"),
        temp_label=unit_ctx["units"]["temp_label"],
        trip=trip,
    )
    if not chart_html:
        return HTMLResponse(
            '<p class="text-base-content/30 text-sm py-4 text-center">No temperature data available for this trip.</p>'
        )
    return HTMLResponse(chart_html)


@router.get("/sessions/{trip_id}/charts/drive", response_class=HTMLResponse)
async def trip_drive_chart(
    request: Request,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip or not trip.start_time or not trip.end_time:
        return HTMLResponse(
            '<p class="text-base-content/30 text-sm py-4 text-center">No drive data available for this trip.</p>'
        )
    battery_df = await query_trip_battery_series(db, trip.device_id, trip.start_time, trip.end_time)
    vehicle_df = await query_trip_vehicle_series(db, trip.device_id, trip.start_time, trip.end_time)
    unit_ctx = await get_unit_context(db)
    distance_factor = MI_PER_KM if unit_ctx["distance_unit"] == "us" else 1.0
    chart_html = build_drive_graph(
        battery_df,
        vehicle_df,
        distance_factor=distance_factor,
        range_label=unit_ctx["units"]["range_label"],
        speed_label=unit_ctx["units"]["speed_label"],
        trip=trip,
    )
    if not chart_html:
        return HTMLResponse(
            '<p class="text-base-content/30 text-sm py-4 text-center">No drive data available for this trip.</p>'
        )
    return HTMLResponse(chart_html)


@router.get("/sessions/{trip_id}/expanded", response_class=HTMLResponse)
async def trip_expanded(
    request: Request,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        return HTMLResponse(
            '<p class="text-base-content/30 p-4">Trip not found.</p>',
            status_code=404,
        )
    battery_df = pd.DataFrame()
    vehicle_df = pd.DataFrame()
    if trip.start_time and trip.end_time:
        battery_df = await query_trip_battery_series(db, trip.device_id, trip.start_time, trip.end_time)
        vehicle_df = await query_trip_vehicle_series(db, trip.device_id, trip.start_time, trip.end_time)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    unit_ctx = await get_unit_context(db)
    distance_factor = MI_PER_KM if unit_ctx["distance_unit"] == "us" else 1.0
    temp_factor_f = unit_ctx["temp_unit"] == "us"
    context = {
        **unit_ctx,
        "trip": trip,
        "battery_chart": build_expanded_battery_chart(
            battery_df,
            distance_factor=distance_factor,
            range_label=unit_ctx["units"]["range_label"],
            temp_factor_f=temp_factor_f,
            temp_label=unit_ctx["units"]["temp_label"],
        ),
        "environment_chart": build_expanded_environment_chart(
            vehicle_df,
            temp_factor_f=temp_factor_f,
            temp_label=unit_ctx["units"]["temp_label"],
        ),
        "driving_chart": build_expanded_driving_chart(
            vehicle_df,
            distance_factor=distance_factor,
            speed_label=unit_ctx["units"]["speed_label"],
        ),
        "user_tz": user_tz,
    }
    return templates.TemplateResponse(request, "driving/sessions/partials/expanded_modal.html", context)


@router.get("/sessions/new", response_class=HTMLResponse)
async def new_trip_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    unit_ctx = await get_unit_context(db)
    context = {
        **unit_ctx,
        "default_date": date.today().isoformat(),
    }
    return templates.TemplateResponse(request, "driving/sessions/partials/add_form.html", context)


@router.post("/sessions", response_class=HTMLResponse)
async def create_trip(
    request: Request,
    db: AsyncSession = Depends(get_db),
    trip_date: Annotated[str | None, Form()] = None,
    distance: Annotated[float | None, Form()] = None,
    duration_minutes: Annotated[float | None, Form()] = None,
    energy_consumed: Annotated[float | None, Form()] = None,
    efficiency: Annotated[float | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    hx_request: Annotated[str | None, Header()] = None,
):
    if not trip_date:
        return HTMLResponse(
            content="<p class='text-error text-sm p-2'>Date is required.</p>",
            status_code=422,
        )

    # Parse trip_date as local-time (midnight) in the user's configured TZ so
    # the resulting UTC instant lines up with their day, matching how charging
    # sessions handle date-only form inputs.
    create_trip_user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
    try:
        parsed_date = parse_user_local_to_utc(trip_date, "00:00", create_trip_user_tz)
    except ValueError:
        return HTMLResponse(
            content="<p class='text-error text-sm p-2'>Invalid date format.</p>",
            status_code=422,
        )

    # Convert user-entered distance + efficiency to metric (km, km/kWh) for storage
    unit_ctx = await get_unit_context(db)
    distance_km = to_metric_distance(distance, unit_ctx["distance_unit"]) if distance else None
    efficiency_metric = (
        to_metric_distance(efficiency, unit_ctx["distance_unit"]) if efficiency else None
    )

    # Auto-calculate efficiency if distance and energy provided (in metric)
    calc_efficiency = efficiency_metric
    if calc_efficiency is None and distance_km and energy_consumed and energy_consumed > 0:
        calc_efficiency = distance_km / energy_consumed

    # Get active device
    active_vehicle = await get_active_vehicle(db)
    device_id = active_vehicle.device_id if active_vehicle else "manual"

    # Compute deterministic trip_id over the user-supplied fields. The model
    # default was dropped in the trip-overhaul migration; manual-trip writes
    # now own their dedup invariant. Two manual entries with identical
    # (device_id, parsed_date, distance) intentionally collide so re-submitting
    # the same data is a no-op rather than silently doubling rows.
    manual_trip_id = uuid.uuid5(
        LIGHTNINGROD_TRIP_NAMESPACE,
        f"manual|{device_id}|{parsed_date.isoformat()}|{distance_km or 0}",
    )

    new_trip = EVTripMetrics(
        trip_id=manual_trip_id,
        device_id=device_id,
        end_time=parsed_date,
        start_time=parsed_date,
        distance=distance_km,
        # duration column is canonical seconds; the form field is minutes.
        duration=duration_minutes * 60 if duration_minutes is not None else None,
        energy_consumed=energy_consumed,
        efficiency=calc_efficiency,
        source_system="manual",
        is_complete=True,
    )

    db.add(new_trip)
    await db.commit()

    # Return HX-Trigger to refresh the trip list, or redirect
    if hx_request:
        response = Response(
            content="",
            status_code=200,
            headers={
                "HX-Trigger": "trip-created",
            },
        )
        return response

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/driving/sessions", status_code=303)


@router.delete("/sessions/{trip_id}")
async def delete_trip(
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if trip is None:
        return HTMLResponse(content="Trip not found.", status_code=404)

    await db.delete(trip)
    await db.commit()

    return Response(
        content="",
        status_code=200,
        headers={
            "HX-Trigger": "trip-deleted",
            "HX-Reswap": "none",
        },
    )
