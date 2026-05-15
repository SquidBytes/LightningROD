"""Charging-cost analytics routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.comparisons import query_gas_comparison
from web.queries.cost_explorer import (
    build_cost_explorer_monthly_chart,
    get_charge_type_network_groupings,
    query_cost_explorer,
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

# Human-readable labels for the preset date-range buttons (see filter_bar.html).
RANGE_LABELS: dict[str, str] = {
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "ytd": "Year to date",
    "1y": "Last 12 months",
    "all": "All time",
}


@router.get("/costs", response_class=HTMLResponse)
async def costs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: str | None = "all",
    section: str | None = None,
    networks: str | None = None,
    free_what_if: str | None = None,
    free_what_if_scope: str | None = "global",
    free_what_if_networks: str | None = None,
    ref_mode: str | None = "network",
    ref_network_id: int | None = None,
    ref_value: float | None = None,
    hx_request: Annotated[str | None, Header()] = None,
):
    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    # Parse Cost Explorer control params + resolve reference rate.
    def _parse_csv_ids(raw: str | None) -> list[int]:
        if not raw:
            return []
        return [int(x) for x in raw.split(",") if x.strip().isdigit()]

    selected_network_ids = _parse_csv_ids(networks)
    free_what_if_network_ids = _parse_csv_ids(free_what_if_networks)
    free_what_if_bool = free_what_if in ("1", "true", "on")
    ref_mode_eff = ref_mode or "network"

    all_networks = await get_all_networks(db)
    networks_by_id_lookup = {n.id: n for n in all_networks}

    # Networks grouped by session-history AC/DC mix — powers the Network filter
    # quick-action chips (All / None / AC / DC). A network can appear in both
    # groupings if the user has both AC and DC sessions on it.
    charge_type_groupings = await get_charge_type_network_groupings(
        db, time_range=range or "all", device_id=active_device_id
    )

    if ref_mode_eff == "custom":
        reference_rate = float(ref_value) if (ref_value is not None and ref_value > 0) else 0.0
        ref_network_name_eff = None
        ref_network_id_eff = None
    else:
        ref_net = networks_by_id_lookup.get(ref_network_id) if ref_network_id else None
        if ref_net is None:
            non_free_networks = [n for n in all_networks if not n.is_free and n.cost_per_kwh]
            ref_net = non_free_networks[0] if non_free_networks else None
        reference_rate = float(ref_net.cost_per_kwh or 0) if ref_net else 0.0
        ref_network_name_eff = ref_net.network_name if ref_net else None
        ref_network_id_eff = ref_net.id if ref_net else None

    unit_ctx = await get_unit_context(db)

    cost_explorer = await query_cost_explorer(
        db,
        time_range=range or "all",
        device_id=active_device_id,
        network_ids=selected_network_ids or None,
        free_charging_what_if=free_what_if_bool,
        free_charging_scope=free_what_if_scope or "global",
        free_charging_networks=free_what_if_network_ids or None,
        reference_rate=reference_rate,
        reference_network_id=ref_network_id_eff,
        reference_network_name=ref_network_name_eff,
    )
    cost_explorer_chart = build_cost_explorer_monthly_chart(
        cost_explorer["monthly_trend"],
        reference_network_name=ref_network_name_eff,
    )

    # Summary strip is range-scoped — it reflects every network for the range,
    # ignoring the in-card network filter (which scopes the aside + ledger).
    # Re-run the aggregator unfiltered for the strip; reuse the filtered result
    # directly when no filter is active to skip the redundant query.
    if selected_network_ids:
        cost_explorer_strip = await query_cost_explorer(
            db,
            time_range=range or "all",
            device_id=active_device_id,
            reference_rate=reference_rate,
        )
    else:
        cost_explorer_strip = cost_explorer

    # $/mile honors the user's distance-unit setting. cost_per_distance_km is
    # the raw $/km ratio; multiply by km-per-display-unit so a US user sees
    # $/mile and a metric user sees $/km.
    strip_distance_factor = 1.0 / MI_PER_KM if unit_ctx["distance_unit"] == "us" else 1.0
    cost_per_distance = cost_explorer_strip.get("cost_per_distance_km")
    cost_explorer_strip = {
        **cost_explorer_strip,
        "cost_per_distance": (
            round(cost_per_distance * strip_distance_factor, 4)
            if cost_per_distance is not None
            else None
        ),
    }

    selected_network_items = [
        {"id": str(nid), "label": networks_by_id_lookup[nid].network_name}
        for nid in selected_network_ids
        if nid in networks_by_id_lookup
    ]

    # Load comparison settings
    toggle_keys = ["comparison_section_visible", "comparison_gas_enabled", "comparison_network_enabled"]
    toggles = await get_app_settings_dict(db, toggle_keys)
    show_comparisons = toggles.get("comparison_section_visible", "true") != "false"

    gas_comparison = None

    if show_comparisons and toggles.get("comparison_gas_enabled", "true") != "false":
        default_ice = await get_default_ice_vehicle(db)
        gas_comparison = await query_gas_comparison(
            db,
            device_id=active_device_id,
            vehicle=active_vehicle,
            ice_vehicle=default_ice,
            time_range=range or "all",
        )

    all_vehicles = await get_all_vehicles(db)
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

    active_range = range or "all"
    range_label = RANGE_LABELS.get(active_range, "Custom range")

    if section == "cost_explorer":
        # Body-only partial — replaces #cost-explorer-body inside the shell, so
        # the form/header strip is NOT re-emitted (avoids nested duplication).
        # The free-charging what-if controls now live IN the aside (their effect
        # is only visible there + on the chart), so they need their state passed
        # to the section partial too.
        section_context = {
            **unit_ctx,
            "cost_explorer": cost_explorer,
            "cost_explorer_chart": cost_explorer_chart,
            "active_range": active_range,
            "free_what_if": free_what_if_bool,
            "free_what_if_scope": free_what_if_scope or "global",
            "free_what_if_networks_csv": free_what_if_networks or "",
        }
        return templates.TemplateResponse(request, "costs/partials/cost_explorer_body.html", section_context)

    context = {
        **unit_ctx,
        "cost_explorer": cost_explorer,
        "cost_explorer_strip": cost_explorer_strip,
        "cost_explorer_chart": cost_explorer_chart,
        "active_range": active_range,
        "range_label": range_label,
        "active_page": "costs",
        "page_title": "Costs",
        "show_comparisons": show_comparisons,
        "gas_comparison": gas_comparison,
        "networks": all_networks,
        "selected_networks_csv": networks or "",
        "selected_network_items": selected_network_items,
        "ac_network_ids": charge_type_groupings["ac"],
        "dc_network_ids": charge_type_groupings["dc"],
        "free_what_if": free_what_if_bool,
        "free_what_if_scope": free_what_if_scope or "global",
        "free_what_if_networks_csv": free_what_if_networks or "",
        "ref_mode": ref_mode_eff,
        "ref_network_id": ref_network_id_eff,
        "ref_value": ref_value,
        "toggles": toggles,
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
    }

    if hx_request:
        return templates.TemplateResponse(request, "costs/partials/costs_body.html", context)
    return templates.TemplateResponse(request, "costs/index.html", context)
