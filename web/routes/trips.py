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
    detect_trip_locations,
    query_trip_battery_series,
    query_trip_vehicle_series,
    query_trips,
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

    start_location, end_location = None, None
    if trip.start_time and trip.end_time:
        start_location, end_location = await detect_trip_locations(
            db, trip.device_id, trip.start_time, trip.end_time
        )

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
