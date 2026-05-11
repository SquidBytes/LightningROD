"""Charging-cost analytics routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.comparisons import query_gas_comparison, query_network_comparison
from web.queries.costs import (
    avg_cost_per_session,
    build_monthly_cost_chart,
    build_network_cost_chart,
    cost_per_kwh,
    cost_per_mile,
    free_charging_savings,
    query_cost_summary,
    query_monthly_costs,
    query_subscription_savings,
)
from web.queries.ice_vehicles import get_default_ice_vehicle
from web.queries.settings import (
    get_all_networks,
    get_app_settings_dict,
    get_unit_context,
)
from web.queries.vehicles import (
    get_active_device_id,
    get_active_vehicle,
    get_all_vehicles,
)
from web.unit_system import MI_PER_KM

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/costs", response_class=HTMLResponse)
async def costs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: str | None = "all",
    hx_request: Annotated[str | None, Header()] = None,
):
    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    summary = await query_cost_summary(db, time_range=range or "all", device_id=active_device_id)
    monthly = await query_monthly_costs(db, time_range=range or "all", device_id=active_device_id)
    subscription_savings = await query_subscription_savings(db, time_range=range or "all", device_id=active_device_id)

    # Summary-row ratios + free-charging sub-line.
    # Helpers return None when denominator is 0 — pre-format to "—" here so
    # the template never sees NaN/$0.00 for empty ranges.
    avg_per_session_val = await avg_cost_per_session(
        db, device_id=active_device_id, time_range=range or "all"
    )
    cost_per_mile_val = await cost_per_mile(
        db, device_id=active_device_id, time_range=range or "all"
    )
    cost_per_kwh_val = await cost_per_kwh(
        db, device_id=active_device_id, time_range=range or "all"
    )
    free_charging_savings_val = await free_charging_savings(
        db, device_id=active_device_id, time_range=range or "all"
    )

    def _fmt_dollars(v):
        return "—" if v is None else f"${v:,.2f}"

    avg_per_session_formatted = _fmt_dollars(avg_per_session_val)
    cost_per_mile_formatted = _fmt_dollars(cost_per_mile_val)
    cost_per_kwh_formatted = _fmt_dollars(cost_per_kwh_val)

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
            default_ice = await get_default_ice_vehicle(db)
            gas_comparison = await query_gas_comparison(
                db,
                device_id=active_device_id,
                vehicle=active_vehicle,
                ice_vehicle=default_ice,
                time_range=range or "all",
            )

        networks = all_networks
        if toggles.get("comparison_network_enabled", "true") != "false":
            ref_rate_param = request.query_params.get("ref_rate")
            if ref_rate_param:
                reference_rate = float(ref_rate_param)
            else:
                non_free_networks = [n for n in networks if not n.is_free and n.cost_per_kwh]
                reference_rate = float(non_free_networks[0].cost_per_kwh or 0) if non_free_networks else 0.48
            network_comparison = await query_network_comparison(db, reference_rate, time_range=range or "all")

    all_vehicles = await get_all_vehicles(db)
    unit_ctx = await get_unit_context(db)
    distance_factor = MI_PER_KM if unit_ctx["distance_unit"] == "us" else 1.0

    # Convert gas_comparison total_distance (km) to display units
    if gas_comparison is not None and gas_comparison.get("total_distance") is not None:
        gas_comparison["total_distance"] = gas_comparison["total_distance"] * distance_factor

    # Surface HA sensor friendly names for the ledger surface.
    # Fallback is the raw entity_id when the setting is blank; the template
    # renders the entity_id as-is rather than a hardcoded label.
    if gas_comparison is not None:
        sensor_settings = await get_app_settings_dict(
            db,
            ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"],
        )
        gas_comparison["station_friendly_name"] = (
            sensor_settings.get("gas_sensor_station_entity_id") or ""
        )
        gas_comparison["average_friendly_name"] = (
            sensor_settings.get("gas_sensor_average_entity_id") or ""
        )

    context = {
        **unit_ctx,
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
        "subscription_savings": subscription_savings,
        # Summary-row ratios
        "avg_per_session_val": avg_per_session_val,
        "cost_per_mile_val": cost_per_mile_val,
        "cost_per_kwh_val": cost_per_kwh_val,
        "free_charging_savings_val": free_charging_savings_val,
        "avg_per_session_formatted": avg_per_session_formatted,
        "cost_per_mile_formatted": cost_per_mile_formatted,
        "cost_per_kwh_formatted": cost_per_kwh_formatted,
    }

    if hx_request:
        return templates.TemplateResponse(request, "costs/partials/summary_cards.html", context)
    return templates.TemplateResponse(request, "costs/index.html", context)
