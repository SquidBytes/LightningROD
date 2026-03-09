from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
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
from web.queries.vehicles import get_active_vehicle, get_all_vehicles, set_active_vehicle

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Home page shows GLOBAL stats across all vehicles (device_id=None)
    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)

    # Summary cards (global -- all vehicles)
    summary = await query_dashboard_summary(db, device_id=None)

    # Cost data for energy-by-network donut (global)
    cost_summary = await query_cost_summary(db, device_id=None)

    # Build network colors map and id->name map for chart builders
    networks = await get_all_networks(db)
    network_colors = {n.network_name: (n.color or '#6B7280') for n in networks}
    network_id_to_name = {n.id: n.network_name for n in networks}

    # Build energy-by-network donut chart with network colors
    energy_by_network_chart = build_energy_by_network_chart(
        cost_summary["by_network"],
        network_colors=network_colors,
    )

    # Build monthly energy by network stacked bar chart (all vehicles)
    monthly_sessions_stmt = (
        select(EVChargingSession)
        .where(EVChargingSession.energy_kwh.isnot(None))
        .order_by(EVChargingSession.session_start_utc)
    )
    all_sessions_result = await db.execute(monthly_sessions_stmt)
    all_sessions = all_sessions_result.scalars().all()

    monthly_energy_chart = build_monthly_energy_by_network_chart(
        sessions=all_sessions,
        network_id_to_name=network_id_to_name,
        network_colors=network_colors,
    )

    # Charging efficiency aggregates (global)
    efficiency = await query_charging_efficiency(db, device_id=None)

    # Per-vehicle card stats
    vehicle_cards = []
    for vehicle in all_vehicles:
        stats_result = await db.execute(
            select(
                func.count(EVChargingSession.id).label("session_count"),
                func.max(EVChargingSession.session_start_utc).label("last_charge"),
            ).where(EVChargingSession.device_id == vehicle.device_id)
        )
        stats = stats_result.one()
        vehicle_cards.append({
            "vehicle": vehicle,
            "session_count": stats.session_count or 0,
            "last_charge": stats.last_charge,
        })

    # Get user timezone for template
    app_settings = await get_app_settings_dict(db, ["user_timezone"])
    user_tz = app_settings.get("user_timezone", "UTC")

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "page_title": "Home",
            "active_page": "dashboard",
            "summary": summary,
            "monthly_energy_chart": monthly_energy_chart,
            "energy_by_network_chart": energy_by_network_chart,
            "efficiency": efficiency,
            "active_vehicle": active_vehicle,
            "all_vehicles": all_vehicles,
            "vehicle_cards": vehicle_cards,
            "user_tz": user_tz,
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
