"""Route handler for /driving/performance (Driving Analytics).

Mirrors web/routes/performance.py structure. Mounted via
`app.include_router(driving_performance.router, prefix="/driving")`
so the handler path `/performance` becomes the external `/driving/performance`.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.driving_performance import (
    _min_points_for_range,
    build_regen_recovery_chart,
    build_temperature_correlation_chart,
    query_driving_performance_summary,
    query_regen_per_trip,
    query_temperature_correlation,
)
from web.queries.settings import get_unit_context
from web.queries.trips import build_efficiency_trend_chart
from web.queries.vehicles import (
    get_active_device_id,
    get_active_vehicle,
    get_all_vehicles,
)
from web.unit_system import MI_PER_KM

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/performance", response_class=HTMLResponse)
async def driving_performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: Optional[str] = "30d",
    hx_request: Annotated[Optional[str], Header()] = None,
):
    time_range = range or "30d"

    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)

    # Unit context: metric DB -> display conversion
    unit_ctx = await get_unit_context(db)
    distance_unit = unit_ctx["distance_unit"]
    distance_factor = MI_PER_KM if distance_unit == "us" else 1.0
    efficiency_factor = distance_factor
    range_factor = distance_factor

    summary = await query_driving_performance_summary(
        db, time_range=time_range, device_id=active_device_id,
    )

    # Convert metric base -> display units
    if summary.get("total_distance") is not None:
        summary["total_distance"] = summary["total_distance"] * distance_factor
    if summary.get("avg_driving_efficiency") is not None:
        summary["avg_driving_efficiency"] = (
            summary["avg_driving_efficiency"] * efficiency_factor
        )
    if summary.get("total_regen") is not None:
        summary["total_regen"] = summary["total_regen"] * range_factor
    if summary.get("range_recovered") is not None:
        summary["range_recovered"] = summary["range_recovered"] * range_factor

    # Driving Efficiency chart (moved from /trips)
    driving_efficiency_chart = build_efficiency_trend_chart(
        summary.get("efficiency_trend") or [],
        efficiency_factor=efficiency_factor,
        efficiency_label=unit_ctx["units"]["efficiency_label"],
    )

    # Temperature vs efficiency scatter (Phase 25 Plan 25-03).
    temp_data = await query_temperature_correlation(
        db, time_range=time_range, device_id=active_device_id
    )
    temperature_scatter_chart = build_temperature_correlation_chart(
        temp_data,
        distance_unit=distance_unit,
        min_points=_min_points_for_range(time_range),
    )

    # Regen recovery dual-axis bar chart (Phase 25 Plan 25-03).
    regen_trips = await query_regen_per_trip(
        db, time_range=time_range, device_id=active_device_id
    )
    regen_bar_chart = build_regen_recovery_chart(regen_trips)

    context = {
        **unit_ctx,
        "summary": summary,
        "driving_efficiency_chart": driving_efficiency_chart,
        "temperature_scatter_chart": temperature_scatter_chart,
        "regen_bar_chart": regen_bar_chart,
        "active_range": time_range,
        "active_page": "driving_performance",
        "page_title": "Driving Analytics",
        "unit_label": unit_ctx["units"]["efficiency_label"],
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
    }

    if hx_request:
        return templates.TemplateResponse(
            request, "driving/performance/partials/summary.html", context
        )
    return templates.TemplateResponse(
        request, "driving/performance/index.html", context
    )
