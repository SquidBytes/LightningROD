from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
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

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Summary cards
    summary = await query_dashboard_summary(db)

    # Cost data for energy-by-network donut
    cost_summary = await query_cost_summary(db)

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
    all_sessions_result = await db.execute(
        select(EVChargingSession)
        .where(EVChargingSession.energy_kwh.isnot(None))
        .order_by(EVChargingSession.session_start_utc)
    )
    all_sessions = all_sessions_result.scalars().all()

    monthly_energy_chart = build_monthly_energy_by_network_chart(
        sessions=all_sessions,
        network_id_to_name=network_id_to_name,
        network_colors=network_colors,
    )

    # Charging efficiency aggregates (EVSE loss + utilization)
    efficiency = await query_charging_efficiency(db)

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
        },
    )
