from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.comparisons import query_gas_comparison, query_network_comparison
from web.queries.costs import (
    build_monthly_cost_chart,
    build_network_cost_chart,
    query_cost_summary,
    query_monthly_costs,
)
from web.queries.settings import get_all_networks, get_app_settings_dict
from web.queries.vehicles import get_active_device_id, get_active_vehicle, get_all_vehicles

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/costs", response_class=HTMLResponse)
async def costs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: Optional[str] = "all",
    hx_request: Annotated[Optional[str], Header()] = None,
):
    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    summary = await query_cost_summary(db, time_range=range or "all", device_id=active_device_id)
    monthly = await query_monthly_costs(db, time_range=range or "all", device_id=active_device_id)

    # Build network colors map for consistent chart coloring
    all_networks = await get_all_networks(db)
    network_colors = {n.network_name: (n.color or '#6B7280') for n in all_networks}

    network_chart = build_network_cost_chart(summary["by_network"], network_colors=network_colors)
    monthly_chart = build_monthly_cost_chart(monthly, network_colors=network_colors)

    # Load comparison settings
    toggle_keys = ["comparison_section_visible", "comparison_gas_enabled", "comparison_network_enabled"]
    toggles = await get_app_settings_dict(db, toggle_keys)
    show_comparisons = toggles.get("comparison_section_visible", "true") != "false"

    gas_comparison = None
    network_comparison = None
    networks = []

    if show_comparisons:
        if toggles.get("comparison_gas_enabled", "true") != "false":
            gas_comparison = await query_gas_comparison(db, time_range=range or "all")

        networks = all_networks
        if toggles.get("comparison_network_enabled", "true") != "false":
            ref_rate_param = request.query_params.get("ref_rate")
            if ref_rate_param:
                reference_rate = float(ref_rate_param)
            else:
                non_free_networks = [n for n in networks if not n.is_free and n.cost_per_kwh]
                reference_rate = float(non_free_networks[0].cost_per_kwh) if non_free_networks else 0.48
            network_comparison = await query_network_comparison(db, reference_rate, time_range=range or "all")

    all_vehicles = await get_all_vehicles(db)

    context = {
        "summary": summary,
        "network_chart": network_chart,
        "monthly_chart": monthly_chart,
        "active_range": range or "all",
        "active_page": "costs",
        "page_title": "Costs",
        "show_comparisons": show_comparisons,
        "gas_comparison": gas_comparison,
        "network_comparison": network_comparison,
        "networks": networks,
        "toggles": toggles,
        "network_colors": network_colors,
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
    }

    if hx_request:
        return templates.TemplateResponse(request, "costs/partials/summary_cards.html", context)
    return templates.TemplateResponse(request, "costs/index.html", context)
