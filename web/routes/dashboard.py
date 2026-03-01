from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.dependencies import get_db
from web.queries.dashboard import (
    build_dashboard_efficiency_chart,
    build_energy_by_network_chart,
    query_dashboard_summary,
)
from web.queries.costs import (
    build_monthly_cost_chart,
    query_cost_summary,
    query_monthly_costs,
)
from web.queries.settings import get_all_networks, get_app_settings_dict

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

UNIT_CONFIG = {
    "us": {"label": "mi/kWh", "factor": 1.0},
    "eu": {"label": "km/kWh", "factor": 1.60934},
}


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Summary cards
    summary = await query_dashboard_summary(db)

    # Cost data for charts
    cost_summary = await query_cost_summary(db)
    monthly = await query_monthly_costs(db)

    # Build network colors map for consistent chart coloring
    networks = await get_all_networks(db)
    network_colors = {n.network_name: (n.color or '#6B7280') for n in networks}

    # Build monthly cost trend chart with network colors
    monthly_cost_chart = build_monthly_cost_chart(monthly, network_colors=network_colors)

    # Build energy-by-network donut chart with network colors
    energy_by_network_chart = build_energy_by_network_chart(
        cost_summary["by_network"],
        network_colors=network_colors,
    )

    # Build efficiency trend chart
    # Query sessions with miles_added for efficiency scatter
    eff_result = await db.execute(
        select(EVChargingSession)
        .where(EVChargingSession.miles_added.isnot(None))
        .order_by(EVChargingSession.session_start_utc)
    )
    efficiency_sessions = eff_result.scalars().all()

    # Read unit preference
    unit_settings = await get_app_settings_dict(db, ["efficiency_unit"])
    unit_pref = unit_settings.get("efficiency_unit") or "us"
    unit = UNIT_CONFIG.get(unit_pref, UNIT_CONFIG["us"])

    efficiency_chart = build_dashboard_efficiency_chart(
        sessions=efficiency_sessions,
        unit_label=unit["label"],
        unit_factor=unit["factor"],
    )

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "page_title": "Dashboard",
            "active_page": "dashboard",
            "summary": summary,
            "monthly_cost_chart": monthly_cost_chart,
            "energy_by_network_chart": energy_by_network_chart,
            "efficiency_chart": efficiency_chart,
            "unit_label": unit["label"],
        },
    )
