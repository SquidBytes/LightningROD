import math
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.dependencies import get_db
from web.queries.trips import (
    build_driving_score_radar,
    build_efficiency_trend_chart,
    query_efficiency_trend,
    query_trips,
)
from web.queries.vehicles import get_active_device_id, get_active_vehicle, get_all_vehicles

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

PER_PAGE = 25


@router.get("/trips", response_class=HTMLResponse)
async def trips(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: Optional[str] = "30d",
    sort: Optional[str] = "date",
    dir: Optional[str] = "desc",
    page: int = 1,
    hx_request: Annotated[Optional[str], Header()] = None,
):
    time_range = range or "30d"
    sort_by = sort or "date"
    sort_dir = dir or "desc"

    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)

    # Query trips and efficiency trend
    trip_list, total, summary = await query_trips(
        db=db,
        page=page,
        per_page=PER_PAGE,
        date_preset=time_range,
        sort_by=sort_by,
        sort_dir=sort_dir,
        device_id=active_device_id,
    )

    trend_data = await query_efficiency_trend(db, time_range=time_range, device_id=active_device_id)
    trend_chart = build_efficiency_trend_chart(trend_data)

    # Pagination
    total_pages = max(math.ceil(total / PER_PAGE), 1)
    has_prev = page > 1
    has_next = page < total_pages

    context = {
        "trips": trip_list,
        "total": total,
        "summary": summary,
        "trend_chart": trend_chart,
        "active_range": time_range,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "page": page,
        "total_pages": total_pages,
        "has_prev": has_prev,
        "has_next": has_next,
        "per_page": PER_PAGE,
        "active_page": "trips",
        "page_title": "Trip History",
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
    }

    if hx_request:
        return templates.TemplateResponse(request, "trips/partials/summary.html", context)
    return templates.TemplateResponse(request, "trips/index.html", context)


@router.get("/trips/{trip_id}/detail", response_class=HTMLResponse)
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

    context = {
        "trip": trip,
        "radar_chart": radar_chart,
    }
    return templates.TemplateResponse(request, "trips/partials/trip_detail.html", context)


@router.get("/trips/new", response_class=HTMLResponse)
async def new_trip_form(
    request: Request,
):
    context = {
        "default_date": date.today().isoformat(),
    }
    return templates.TemplateResponse(request, "trips/partials/add_form.html", context)


@router.post("/trips", response_class=HTMLResponse)
async def create_trip(
    request: Request,
    db: AsyncSession = Depends(get_db),
    trip_date: Annotated[Optional[str], Form()] = None,
    distance: Annotated[Optional[float], Form()] = None,
    duration_minutes: Annotated[Optional[float], Form()] = None,
    energy_consumed: Annotated[Optional[float], Form()] = None,
    efficiency: Annotated[Optional[float], Form()] = None,
    notes: Annotated[Optional[str], Form()] = None,
    hx_request: Annotated[Optional[str], Header()] = None,
):
    if not trip_date:
        return HTMLResponse(
            content="<p class='text-error text-sm p-2'>Date is required.</p>",
            status_code=422,
        )

    try:
        parsed_date = datetime.fromisoformat(f"{trip_date}T00:00:00").replace(tzinfo=timezone.utc)
    except ValueError:
        return HTMLResponse(
            content="<p class='text-error text-sm p-2'>Invalid date format.</p>",
            status_code=422,
        )

    # Auto-calculate efficiency if distance and energy provided
    calc_efficiency = efficiency
    if calc_efficiency is None and distance and energy_consumed and energy_consumed > 0:
        calc_efficiency = distance / energy_consumed

    # Get active device
    active_vehicle = await get_active_vehicle(db)
    device_id = active_vehicle.device_id if active_vehicle else "manual"

    new_trip = EVTripMetrics(
        device_id=device_id,
        end_time=parsed_date,
        start_time=parsed_date,
        distance=distance,
        duration=duration_minutes,
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
    return RedirectResponse(url="/trips", status_code=303)
