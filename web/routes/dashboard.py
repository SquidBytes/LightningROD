from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.dependencies import get_db
from web.queries.dashboard import (
    build_energy_by_network_chart,
    build_monthly_energy_by_network_chart,
    query_charging_efficiency,
    query_dashboard_summary,
)
from web.queries.costs import (
    query_cost_summary,
)
from web.queries.settings import get_all_networks, get_app_settings_dict
from web.queries.vehicles import get_active_device_id, get_active_vehicle, get_all_vehicles, set_active_vehicle

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    # Summary cards
    summary = await query_dashboard_summary(db, device_id=active_device_id)

    # Cost data for energy-by-network donut
    cost_summary = await query_cost_summary(db, device_id=active_device_id)

    # Build network colors map and id->name map for chart builders
    networks = await get_all_networks(db)
    network_colors = {n.network_name: (n.color or '#6B7280') for n in networks}
    network_id_to_name = {n.id: n.network_name for n in networks}

    # Build energy-by-network donut chart with network colors
    energy_by_network_chart = build_energy_by_network_chart(
        cost_summary["by_network"],
        network_colors=network_colors,
    )

    # Build monthly energy by network stacked bar chart
    monthly_sessions_stmt = (
        select(EVChargingSession)
        .where(EVChargingSession.energy_kwh.isnot(None))
        .order_by(EVChargingSession.session_start_utc)
    )
    if active_device_id:
        monthly_sessions_stmt = monthly_sessions_stmt.where(
            EVChargingSession.device_id == active_device_id
        )
    all_sessions_result = await db.execute(monthly_sessions_stmt)
    all_sessions = all_sessions_result.scalars().all()

    monthly_energy_chart = build_monthly_energy_by_network_chart(
        sessions=all_sessions,
        network_id_to_name=network_id_to_name,
        network_colors=network_colors,
    )

    # Charging efficiency aggregates (EVSE loss + utilization)
    efficiency = await query_charging_efficiency(db, device_id=active_device_id)

    all_vehicles = await get_all_vehicles(db)

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "page_title": "Dashboard",
            "active_page": "dashboard",
            "summary": summary,
            "monthly_energy_chart": monthly_energy_chart,
            "energy_by_network_chart": energy_by_network_chart,
            "efficiency": efficiency,
            "active_vehicle": active_vehicle,
            "all_vehicles": all_vehicles,
        },
    )


@router.post("/vehicles/{vehicle_id}/activate")
async def activate_vehicle(
    vehicle_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redirect_to: str = Form("/charging/sessions"),
):
    """Switch active vehicle and redirect back to current page."""
    await set_active_vehicle(db, vehicle_id)
    return RedirectResponse(url=redirect_to, status_code=303)
