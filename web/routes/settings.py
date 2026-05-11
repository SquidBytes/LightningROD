"""Settings routes for app options, vehicles, networks, and data sources."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.data_source_config import DataSourceConfig
from db.models.reference import EVChargerStall, EVChargingNetwork, EVLocationLookup
from web import developer_mode as dev_mode_module
from web.dependencies import get_db
from web.queries.gas_prices import (
    delete_gas_price,
    get_all_gas_prices,
    upsert_gas_price,
)
from web.queries.ice_vehicles import (
    create_ice_vehicle,
    delete_ice_vehicle,
    get_all_ice_vehicles,
    get_ice_vehicle_by_id,
    set_default_ice_vehicle,
    update_ice_vehicle,
)
from web.queries.settings import (
    create_location,
    create_network,
    create_stall,
    create_subscription,
    delete_location,
    delete_network,
    delete_stall,
    delete_subscription,
    get_all_networks,
    get_app_setting,
    get_app_settings_dict,
    get_charger_templates,
    get_locations_for_network,
    get_stalls_for_location,
    get_subscriptions_for_network,
    get_unit_context,
    set_app_setting,
    update_location,
    update_network,
    update_stall,
    update_subscription,
)
from web.queries.vehicles import (
    create_vehicle,
    delete_vehicle,
    get_active_vehicle,
    get_all_vehicles,
    get_vehicle_by_id,
    set_active_vehicle,
    update_vehicle,
)
from web.services.csv_parser import get_db_field_options
from web.services.ingestion import supervisor
from web.services.sources.ha_fordpass import adapter as ha_fordpass_adapter
from web.services.sources.ha_fordpass.config import HAFordpassConfig
from web.services.sources.registry import REGISTRY as SOURCE_REGISTRY
from web.services.vehicles.registry import VehicleRegistry
from web.unit_system import (
    convert_fuel_efficiency,
    convert_fuel_volume,
    convert_price_per_volume,
    to_metric_fuel_efficiency,
    to_metric_fuel_volume,
    to_metric_price_per_volume,
)


def _vehicle_presets_for_template() -> list[dict]:
    """Return the Ford preset rows as plain dicts for template + JSON serialization.

    Cascade JS reads dict-keyed JSON; asdict round-trip preserves the wire shape.
    """
    profile = VehicleRegistry.get("Ford")
    return [asdict(r) for r in profile.presets()] if profile else []

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def _network_management_context(db: AsyncSession) -> dict:
    """Build context dict for network_management.html — networks + per-network counts."""
    networks = await get_all_networks(db)
    loc_count_result = await db.execute(
        select(EVLocationLookup.network_id, func.count().label("cnt"))
        .group_by(EVLocationLookup.network_id)
    )
    location_counts = {row.network_id: row.cnt for row in loc_count_result.all()}
    session_count_result = await db.execute(
        select(EVChargingSession.network_id, func.count().label("cnt"))
        .where(EVChargingSession.network_id.isnot(None))
        .group_by(EVChargingSession.network_id)
    )
    session_counts = {row.network_id: row.cnt for row in session_count_result.all()}
    return {"networks": networks, "location_counts": location_counts, "session_counts": session_counts}


async def _vehicle_management_context(db: AsyncSession) -> dict:
    """Build context dict for vehicle_management.html — vehicles + active vehicle + presets."""
    vehicles = await get_all_vehicles(db)
    active_vehicle = await get_active_vehicle(db)
    return {
        "vehicles": vehicles,
        "active_vehicle": active_vehicle,
        "vehicle_presets": _vehicle_presets_for_template(),
        "vehicle_presets_json": json.dumps(_vehicle_presets_for_template()),
    }


async def _ice_vehicle_management_context(db: AsyncSession) -> dict:
    """Build context for ice_vehicle_management.html — rows with display-unit values pre-converted."""
    rows = await get_all_ice_vehicles(db)
    unit_ctx = await get_unit_context(db)
    ice_rows = []
    for r in rows:
        fuel_metric = float(r.fuel_efficiency_l_per_100km) if r.fuel_efficiency_l_per_100km else None
        tank_metric = float(r.tank_capacity_l) if r.tank_capacity_l else None
        ice_rows.append({
            "id": r.id,
            "label": r.label,
            "fuel_efficiency_metric": fuel_metric,
            "fuel_efficiency_display": convert_fuel_efficiency(fuel_metric, unit_ctx["distance_unit"]),
            "tank_capacity_l": tank_metric,
            "tank_capacity_display": convert_fuel_volume(tank_metric, unit_ctx["distance_unit"]),
            "is_default": r.is_default,
        })
    return {**unit_ctx, "ice_vehicles": ice_rows}


SETTINGS_KEYS = [
    "comparison_gas_enabled",
    "comparison_network_enabled",
    "comparison_section_visible",
    "developer_mode",
    "distance_unit",
    "temp_unit",
    "user_timezone",
    "gas_sensor_station_entity_id",
    "gas_sensor_average_entity_id",
]


@router.get("/settings", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tab: str | None = Query(None),
):
    net_ctx = await _network_management_context(db)
    veh_ctx = await _vehicle_management_context(db)
    ice_ctx = await _ice_vehicle_management_context(db)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    if tab == "vehicles":
        active_tab = "vehicles"
    elif tab == "import":
        active_tab = "import"
    elif tab == "networks":
        active_tab = "networks"
    elif tab == "data_sources":
        active_tab = "data_sources"
    elif tab == "fuel":
        active_tab = "fuel"
    else:
        active_tab = "general"

    # Import tab needs extra context for template features and timezone selector
    import_ctx: dict = {}
    if active_tab == "import":
        user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
        import_ctx = {"db_fields": get_db_field_options(), "user_tz": user_tz}

    all_vehicles = await get_all_vehicles(db)
    unit_ctx = await get_unit_context(db)

    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            **unit_ctx,
            **net_ctx,
            **veh_ctx,
            **ice_ctx,
            **import_ctx,
            "settings": settings,
            "active_page": "settings",
            "page_title": "Settings",
            "active_tab": active_tab,
            "all_vehicles": all_vehicles,
        },
    )


# ---------------------------------------------------------------------------
# Vehicle CRUD routes
# ---------------------------------------------------------------------------


@router.get("/settings/vehicles", response_class=HTMLResponse)
async def vehicles_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the vehicle management partial (for HTMX refresh)."""
    veh_ctx = await _vehicle_management_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/vehicle_management.html",
        veh_ctx,
    )


@router.get("/settings/vehicles/new", response_class=HTMLResponse)
async def new_vehicle_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the vehicle add modal form."""
    unit_ctx = await get_unit_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/vehicle_edit_modal.html",
        {
            **unit_ctx,
            "vehicle": None,
            "vehicle_presets_json": json.dumps(_vehicle_presets_for_template()),
        },
    )


@router.post("/settings/vehicles", response_class=HTMLResponse)
async def create_vehicle_route(
    request: Request,
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(""),
    make: str | None = Form(None),
    model: str | None = Form(None),
    year: int | None = Form(None),
    trim_level: str | None = Form(None),
    battery_option: str | None = Form(None),
    battery_capacity_kwh: float | None = Form(None),
    battery_gross_capacity_kwh: float | None = Form(None),
    vin: str | None = Form(None),
    device_id: str | None = Form(None),
):
    if not display_name or not display_name.strip():
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=422,
            content={"detail": "Display name is required"},
        )
    new_vehicle = await create_vehicle(
        db,
        display_name=display_name.strip(),
        make=make or None,
        model=model or None,
        year=year,
        trim_level=trim_level or None,
        battery_option=battery_option or None,
        battery_capacity_kwh=battery_capacity_kwh,
        battery_gross_capacity_kwh=battery_gross_capacity_kwh,
        vin=vin or None,
        device_id=device_id or None,
    )
    veh_ctx = await _vehicle_management_context(db)
    veh_ctx["saved"] = True
    veh_ctx["just_saved_row_id"] = getattr(new_vehicle, "id", None)
    return templates.TemplateResponse(
        request,
        "settings/partials/vehicle_management.html",
        veh_ctx,
    )


@router.get("/settings/vehicles/{vehicle_id}/edit", response_class=HTMLResponse)
async def edit_vehicle_form(
    vehicle_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the vehicle edit modal form with vehicle data + presets."""
    vehicle = await get_vehicle_by_id(db, vehicle_id)
    if vehicle is None:
        return HTMLResponse(status_code=404)
    unit_ctx = await get_unit_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/vehicle_edit_modal.html",
        {
            **unit_ctx,
            "vehicle": vehicle,
            "vehicle_presets_json": json.dumps(_vehicle_presets_for_template()),
        },
    )


@router.put("/settings/vehicles/{vehicle_id}", response_class=HTMLResponse)
async def update_vehicle_route(
    vehicle_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(""),
    make: str | None = Form(None),
    model: str | None = Form(None),
    year: int | None = Form(None),
    trim_level: str | None = Form(None),
    battery_option: str | None = Form(None),
    battery_capacity_kwh: float | None = Form(None),
    battery_gross_capacity_kwh: float | None = Form(None),
    vin: str | None = Form(None),
    device_id: str | None = Form(None),
):
    if not display_name or not display_name.strip():
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=422,
            content={"detail": "Display name is required"},
        )
    await update_vehicle(
        db,
        vehicle_id,
        display_name=display_name.strip(),
        make=make or None,
        model=model or None,
        year=year,
        trim_level=trim_level or None,
        battery_option=battery_option or None,
        battery_capacity_kwh=battery_capacity_kwh,
        battery_gross_capacity_kwh=battery_gross_capacity_kwh,
        vin=vin or None,
        device_id=device_id or None,
    )
    veh_ctx = await _vehicle_management_context(db)
    veh_ctx["saved"] = True
    veh_ctx["just_saved_row_id"] = vehicle_id
    response = templates.TemplateResponse(
        request,
        "settings/partials/vehicle_management.html",
        veh_ctx,
    )
    response.headers["HX-Trigger"] = "closeVehicleModal"
    return response


@router.delete("/settings/vehicles/{vehicle_id}", response_class=HTMLResponse)
async def delete_vehicle_route(
    vehicle_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_vehicle(db, vehicle_id)
    if not deleted:
        return HTMLResponse(
            '<div class="alert alert-error text-sm">Cannot delete the active vehicle. Set another vehicle as active first.</div>',
            status_code=400,
        )
    veh_ctx = await _vehicle_management_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/vehicle_management.html",
        veh_ctx,
    )


@router.post("/settings/vehicles/{vehicle_id}/activate", response_class=HTMLResponse)
async def activate_vehicle_route(
    vehicle_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await set_active_vehicle(db, vehicle_id)
    veh_ctx = await _vehicle_management_context(db)
    response = templates.TemplateResponse(
        request,
        "settings/partials/vehicle_management.html",
        veh_ctx,
    )
    # Active vehicle drives sidebar/header indicator and per-vehicle data on
    # other pages — refresh so they pick up the new selection.
    response.headers["HX-Refresh"] = "true"
    return response


# ---------------------------------------------------------------------------
# ICE Vehicle CRUD routes
# ---------------------------------------------------------------------------


@router.get("/settings/ice-vehicles/new", response_class=HTMLResponse)
async def new_ice_vehicle_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the ICE vehicle add modal body."""
    unit_ctx = await get_unit_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/ice_vehicle_edit_modal.html",
        {
            **unit_ctx,
            "ice": None,
            "fuel_efficiency_display": None,
            "tank_capacity_display": None,
        },
    )


@router.post("/settings/ice-vehicles", response_class=HTMLResponse)
async def create_ice_vehicle_route(
    request: Request,
    db: AsyncSession = Depends(get_db),
    label: str = Form(""),
    fuel_efficiency_display: float | None = Form(None),
    tank_capacity_display: float | None = Form(None),
    is_default: bool = Form(False),
):
    """Create an ICE vehicle. Form values are in display units; converted to metric here."""
    if not label or not label.strip():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": "Label is required"})
    unit_ctx = await get_unit_context(db)
    fuel_metric = to_metric_fuel_efficiency(fuel_efficiency_display, unit_ctx["distance_unit"])
    tank_metric = to_metric_fuel_volume(tank_capacity_display, unit_ctx["distance_unit"])
    if fuel_metric is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": "Combined fuel economy is required"})
    new_ice = await create_ice_vehicle(
        db,
        label=label.strip(),
        fuel_efficiency_l_per_100km=fuel_metric,
        tank_capacity_l=tank_metric,
        is_default=is_default,
    )
    ctx = await _ice_vehicle_management_context(db)
    ctx["saved"] = True
    ctx["just_saved_row_id"] = getattr(new_ice, "id", None)
    response = templates.TemplateResponse(
        request,
        "settings/partials/ice_vehicle_management.html",
        ctx,
    )
    response.headers["HX-Trigger"] = "closeIceVehicleModal"
    return response


@router.get("/settings/ice-vehicles/{ice_id}/edit", response_class=HTMLResponse)
async def edit_ice_vehicle_form(
    ice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the ICE vehicle edit modal body with display-unit values pre-converted."""
    row = await get_ice_vehicle_by_id(db, ice_id)
    if row is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "ICE vehicle not found"})
    unit_ctx = await get_unit_context(db)
    fuel_metric = float(row.fuel_efficiency_l_per_100km) if row.fuel_efficiency_l_per_100km else None
    tank_metric = float(row.tank_capacity_l) if row.tank_capacity_l else None
    return templates.TemplateResponse(
        request,
        "settings/partials/ice_vehicle_edit_modal.html",
        {
            **unit_ctx,
            "ice": row,
            "fuel_efficiency_display": convert_fuel_efficiency(fuel_metric, unit_ctx["distance_unit"]),
            "tank_capacity_display": convert_fuel_volume(tank_metric, unit_ctx["distance_unit"]),
        },
    )


@router.put("/settings/ice-vehicles/{ice_id}", response_class=HTMLResponse)
async def update_ice_vehicle_route(
    ice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    label: str = Form(""),
    fuel_efficiency_display: float | None = Form(None),
    tank_capacity_display: float | None = Form(None),
    is_default: bool = Form(False),
):
    """Update an ICE vehicle. Form values are in display units; converted to metric here."""
    row = await get_ice_vehicle_by_id(db, ice_id)
    if row is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "ICE vehicle not found"})
    if not label or not label.strip():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": "Label is required"})
    unit_ctx = await get_unit_context(db)
    fuel_metric = to_metric_fuel_efficiency(fuel_efficiency_display, unit_ctx["distance_unit"])
    tank_metric = to_metric_fuel_volume(tank_capacity_display, unit_ctx["distance_unit"])
    if fuel_metric is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": "Combined fuel economy is required"})
    await update_ice_vehicle(
        db,
        ice_id,
        label=label.strip(),
        fuel_efficiency_l_per_100km=fuel_metric,
        tank_capacity_l=tank_metric,
        is_default=is_default,
    )
    ctx = await _ice_vehicle_management_context(db)
    ctx["saved"] = True
    ctx["just_saved_row_id"] = ice_id
    response = templates.TemplateResponse(
        request,
        "settings/partials/ice_vehicle_management.html",
        ctx,
    )
    response.headers["HX-Trigger"] = "closeIceVehicleModal"
    return response


@router.delete("/settings/ice-vehicles/{ice_id}", response_class=HTMLResponse)
async def delete_ice_vehicle_route(
    ice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an ICE vehicle. Refuses to delete the default row when others exist."""
    success = await delete_ice_vehicle(db, ice_id)
    ctx = await _ice_vehicle_management_context(db)
    if not success:
        ctx["delete_error"] = "Cannot delete the default ICE vehicle. Set a different default first."
    return templates.TemplateResponse(
        request,
        "settings/partials/ice_vehicle_management.html",
        ctx,
    )


@router.post("/settings/ice-vehicles/{ice_id}/set-default", response_class=HTMLResponse)
async def set_default_ice_vehicle_route(
    ice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Promote one ICE row to default; demote all others."""
    await set_default_ice_vehicle(db, ice_id)
    ctx = await _ice_vehicle_management_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/ice_vehicle_management.html",
        ctx,
    )


# ---------------------------------------------------------------------------
# Network CRUD routes
# ---------------------------------------------------------------------------


@router.post("/settings/networks", response_class=HTMLResponse)
async def create_network_route(
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_name: str = Form(...),
    cost_per_kwh: float | None = Form(None),
    color: str | None = Form(None),
    is_free: str | None = Form(None),
):
    is_free_bool = is_free is not None
    new_network = await create_network(
        db,
        name=network_name,
        cost_per_kwh=cost_per_kwh,
        is_free=is_free_bool,
        color=color,
    )
    net_ctx = await _network_management_context(db)
    net_ctx["saved"] = True
    net_ctx["just_saved_row_id"] = getattr(new_network, "id", None)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        net_ctx,
    )


@router.get("/settings/networks", response_class=HTMLResponse)
async def networks_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the network management partial (used by cancel button to revert edits)."""
    net_ctx = await _network_management_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        net_ctx,
    )


@router.get("/settings/networks/{network_id}/edit", response_class=HTMLResponse)
async def edit_network_row(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    networks = await get_all_networks(db)
    network = next((n for n in networks if n.id == network_id), None)
    if network is None:
        return HTMLResponse(status_code=404)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_edit_row.html",
        {"network": network},
    )


@router.get("/settings/networks/{network_id}/edit-modal", response_class=HTMLResponse)
async def edit_network_modal(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the tabbed network edit modal for the given network."""
    networks = await get_all_networks(db)
    network = next((n for n in networks if n.id == network_id), None)
    if network is None:
        return HTMLResponse(status_code=404)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_edit_modal.html",
        {"network": network},
    )


@router.put("/settings/networks/{network_id}", response_class=HTMLResponse)
async def update_network_route(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_name: str = Form(...),
    cost_per_kwh: float | None = Form(None),
    color: str | None = Form(None),
    is_free: str | None = Form(None),
):
    is_free_bool = is_free is not None
    await update_network(
        db,
        network_id=network_id,
        name=network_name,
        cost_per_kwh=cost_per_kwh,
        is_free=is_free_bool,
        color=color,
    )
    net_ctx = await _network_management_context(db)
    net_ctx["saved"] = True
    net_ctx["just_saved_row_id"] = network_id
    response = templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        net_ctx,
    )
    response.headers["HX-Trigger"] = "closeNetworkModal"
    return response


@router.delete("/settings/networks/{network_id}", response_class=HTMLResponse)
async def delete_network_route(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await delete_network(db, network_id=network_id)
    net_ctx = await _network_management_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        net_ctx,
    )


@router.post("/settings/networks/{network_id}/recalculate", response_class=HTMLResponse)
async def recalculate_network_costs(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Recalculate estimated_cost for all sessions under this network.

    Location-level: recalculates sessions where location has cost_per_kwh set.
    Network-level: recalculates sessions where location has NO cost_per_kwh override.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if not network:
        return HTMLResponse("Network not found", status_code=404)

    locations = await get_locations_for_network(db, network_id)
    location_cost_map = {loc.id: float(loc.cost_per_kwh) for loc in locations if loc.cost_per_kwh is not None}

    sessions_result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.network_id == network_id)
    )
    sessions = sessions_result.scalars().all()

    updated = 0
    for s in sessions:
        if not s.energy_kwh:
            continue
        energy = float(s.energy_kwh)

        if s.location_id and s.location_id in location_cost_map:
            # Location cost override
            s.estimated_cost = location_cost_map[s.location_id] * energy
            updated += 1
        elif network.cost_per_kwh:
            # Network cost (only for sessions WITHOUT location cost override)
            s.estimated_cost = float(network.cost_per_kwh) * energy
            updated += 1

    await db.commit()
    return HTMLResponse(f'<span class="text-success text-sm">{updated} sessions recalculated</span>')


@router.get("/settings/networks/{network_id}/convert-modal", response_class=HTMLResponse)
async def convert_network_modal(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the 'Convert to Location' modal form for a given network."""
    networks = await get_all_networks(db)
    network = next((n for n in networks if n.id == network_id), None)
    if network is None:
        return HTMLResponse(status_code=404)
    other_networks = [n for n in networks if n.id != network_id]
    # Count sessions that will be reassigned
    result = await db.execute(
        select(func.count()).where(EVChargingSession.network_id == network_id)
    )
    session_count = result.scalar() or 0
    return templates.TemplateResponse(
        request,
        "settings/partials/convert_to_location_modal.html",
        {"network": network, "other_networks": other_networks, "session_count": session_count},
    )


@router.post("/settings/networks/{network_id}/convert-to-location", response_class=HTMLResponse)
async def convert_network_to_location(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    target_network_id: int = Form(...),
    location_name: str = Form(...),
    location_type: str | None = Form(None),
):
    """Convert a network into a location under another network.

    - Creates a new location under target_network_id
    - Reassigns all sessions from network_id to target_network_id
    - Sets location_name and location_id on those sessions
    - Deletes the old network
    """
    # Validate source network exists
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        return HTMLResponse("Source network not found", status_code=404)

    # Validate target network exists and is different
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == target_network_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        return HTMLResponse("Target network not found", status_code=404)

    # Create the new location under target network
    new_location = await create_location(
        db,
        network_id=target_network_id,
        name=location_name,
        location_type=location_type or "public",
        cost_per_kwh=source.cost_per_kwh,
    )

    # Reassign all sessions from source network to target network + new location
    sessions_result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.network_id == network_id)
    )
    sessions = sessions_result.scalars().all()
    for s in sessions:
        s.network_id = target_network_id
        s.location_name = location_name
        s.location_id = new_location.id

    # Delete the old network
    await db.delete(source)
    await db.commit()

    net_ctx = await _network_management_context(db)
    response = templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        net_ctx,
    )
    response.headers["HX-Trigger"] = "closeNetworkModal"
    return response


@router.get("/settings/networks/{network_id}/locations", response_class=HTMLResponse)
async def network_locations(
    network_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Return location rows partial for a given network (used in modal)."""
    locations = await get_locations_for_network(db, network_id)
    # Query stall counts per location
    stall_counts: dict[int, int] = {}
    if locations:
        stall_count_result = await db.execute(
            select(EVChargerStall.location_id, func.count().label("cnt"))
            .where(EVChargerStall.location_id.in_([loc.id for loc in locations]))
            .group_by(EVChargerStall.location_id)
        )
        stall_counts = {row.location_id: row.cnt for row in stall_count_result.all()}
    return templates.TemplateResponse(
        request,
        "settings/partials/location_rows.html",
        {"locations": locations, "network_id": network_id, "stall_counts": stall_counts},
    )


@router.get("/settings/networks/{network_id}/locations-summary", response_class=HTMLResponse)
async def network_locations_summary(
    network_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Return read-only location summary for the network table expandable row."""
    locations = await get_locations_for_network(db, network_id)
    stall_counts: dict[int, int] = {}
    stall_types: dict[int, list[str]] = {}
    if locations:
        loc_ids = [loc.id for loc in locations]
        stall_count_result = await db.execute(
            select(EVChargerStall.location_id, func.count().label("cnt"))
            .where(EVChargerStall.location_id.in_(loc_ids))
            .group_by(EVChargerStall.location_id)
        )
        stall_counts = {row.location_id: row.cnt for row in stall_count_result.all()}
        type_result = await db.execute(
            select(EVChargerStall.location_id, EVChargerStall.charger_type)
            .where(EVChargerStall.location_id.in_(loc_ids))
            .where(EVChargerStall.charger_type.isnot(None))
            .distinct()
        )
        for row in type_result.all():
            stall_types.setdefault(row.location_id, []).append(row.charger_type)
    return templates.TemplateResponse(
        request,
        "settings/partials/location_summary.html",
        {"locations": locations, "stall_counts": stall_counts, "stall_types": stall_types},
    )


@router.post("/settings/networks/{network_id}/locations", response_class=HTMLResponse)
async def create_location_route(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_name: str = Form(...),
    location_type: str | None = Form(None),
    notes: str | None = Form(None),
    address: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    cost_per_kwh: float | None = Form(None),
):
    """Add a location under a network."""
    new_loc = await create_location(
        db, network_id, location_name, location_type, notes,
        address=address or None, latitude=latitude, longitude=longitude,
        cost_per_kwh=cost_per_kwh,
    )
    locations = await get_locations_for_network(db, network_id)
    stall_counts: dict[int, int] = {}
    if locations:
        stall_count_result = await db.execute(
            select(EVChargerStall.location_id, func.count().label("cnt"))
            .where(EVChargerStall.location_id.in_([loc.id for loc in locations]))
            .group_by(EVChargerStall.location_id)
        )
        stall_counts = {row.location_id: row.cnt for row in stall_count_result.all()}
    return templates.TemplateResponse(
        request,
        "settings/partials/location_rows.html",
        {
            "locations": locations,
            "network_id": network_id,
            "stall_counts": stall_counts,
            "saved": True,
            "just_saved_row_id": getattr(new_loc, "id", None),
        },
    )


@router.put("/settings/locations/{location_id}", response_class=HTMLResponse)
async def update_location_route(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_name: str = Form(...),
    location_type: str | None = Form(None),
    notes: str | None = Form(None),
    network_id: int = Form(...),
    address: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    cost_per_kwh: float | None = Form(None),
):
    """Update a location and return the refreshed location list."""
    await update_location(
        db, location_id, location_name, location_type, notes,
        address=address or None, latitude=latitude, longitude=longitude,
        cost_per_kwh=cost_per_kwh,
    )
    locations = await get_locations_for_network(db, network_id)
    stall_counts: dict[int, int] = {}
    if locations:
        stall_count_result = await db.execute(
            select(EVChargerStall.location_id, func.count().label("cnt"))
            .where(EVChargerStall.location_id.in_([loc.id for loc in locations]))
            .group_by(EVChargerStall.location_id)
        )
        stall_counts = {row.location_id: row.cnt for row in stall_count_result.all()}
    return templates.TemplateResponse(
        request,
        "settings/partials/location_rows.html",
        {
            "locations": locations,
            "network_id": network_id,
            "stall_counts": stall_counts,
            "saved": True,
            "just_saved_row_id": location_id,
        },
    )


@router.delete("/settings/locations/{location_id}", response_class=HTMLResponse)
async def delete_location_route(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_id: int = 0,
):
    """Delete a location and return the refreshed location list."""
    await delete_location(db, location_id)
    if network_id:
        locations = await get_locations_for_network(db, network_id)
        stall_counts: dict[int, int] = {}
        if locations:
            stall_count_result = await db.execute(
                select(EVChargerStall.location_id, func.count().label("cnt"))
                .where(EVChargerStall.location_id.in_([loc.id for loc in locations]))
                .group_by(EVChargerStall.location_id)
            )
            stall_counts = {row.location_id: row.cnt for row in stall_count_result.all()}
        return templates.TemplateResponse(
            request,
            "settings/partials/location_rows.html",
            {"locations": locations, "network_id": network_id, "stall_counts": stall_counts},
        )
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Stall CRUD routes
# ---------------------------------------------------------------------------


async def _stall_context(db: AsyncSession, location_id: int) -> dict:
    """Build context for stall_rows.html partial."""
    stalls = await get_stalls_for_location(db, location_id)
    # Look up the location's network name for template matching
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    location = result.scalar_one_or_none()
    network_name = None
    has_templates = False
    if location and location.network_id:
        net_result = await db.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == location.network_id)
        )
        network = net_result.scalar_one_or_none()
        if network:
            network_name = network.network_name
            templates = await get_charger_templates(db)
            has_templates = network_name in templates
    return {
        "stalls": stalls,
        "location_id": location_id,
        "network_name": network_name,
        "has_templates": has_templates,
    }


@router.get("/settings/locations/{location_id}/stalls", response_class=HTMLResponse)
async def location_stalls(
    location_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Return stall rows partial for a given location."""
    ctx = await _stall_context(db, location_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/stall_rows.html",
        ctx,
    )


@router.get("/settings/stalls/{stall_id}/edit", response_class=HTMLResponse)
async def stall_edit_modal(
    stall_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: int = 0,
):
    """Return stall edit modal content."""
    result = await db.execute(
        select(EVChargerStall).where(EVChargerStall.id == stall_id)
    )
    stall = result.scalar_one_or_none()
    if not stall:
        return HTMLResponse("<p>Stall not found.</p>", status_code=404)
    loc_id = location_id or stall.location_id
    return templates.TemplateResponse(
        request,
        "settings/partials/stall_edit_modal.html",
        {"stall": stall, "location_id": loc_id},
    )


@router.post("/settings/locations/{location_id}/stalls", response_class=HTMLResponse)
async def create_stall_route(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    stall_label: str = Form(...),
    charger_type: str | None = Form(None),
    rated_kw: float | None = Form(None),
    voltage: float | None = Form(None),
    amperage: float | None = Form(None),
    connector_type: str | None = Form(None),
    notes: str | None = Form(None),
    is_default: str | None = Form(None),
):
    """Create a stall for a location."""
    await create_stall(
        db,
        location_id=location_id,
        label=stall_label,
        charger_type=charger_type or None,
        rated_kw=rated_kw,
        voltage=voltage,
        amperage=amperage,
        connector_type=connector_type or None,
        notes=notes or None,
        is_default=is_default is not None,
    )
    ctx = await _stall_context(db, location_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/stall_rows.html",
        ctx,
    )


@router.put("/settings/stalls/{stall_id}", response_class=HTMLResponse)
async def update_stall_route(
    stall_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: int = Form(...),
    stall_label: str = Form(...),
    charger_type: str | None = Form(None),
    rated_kw: float | None = Form(None),
    voltage: float | None = Form(None),
    amperage: float | None = Form(None),
    connector_type: str | None = Form(None),
    notes: str | None = Form(None),
    is_default: str | None = Form(None),
):
    """Update a stall and return refreshed stall rows."""
    await update_stall(
        db,
        stall_id=stall_id,
        label=stall_label,
        charger_type=charger_type or None,
        rated_kw=rated_kw,
        voltage=voltage,
        amperage=amperage,
        connector_type=connector_type or None,
        notes=notes or None,
        is_default=is_default is not None,
    )
    ctx = await _stall_context(db, location_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/stall_rows.html",
        ctx,
    )


@router.delete("/settings/stalls/{stall_id}", response_class=HTMLResponse)
async def delete_stall_route(
    stall_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: int = 0,
):
    """Delete a stall and return refreshed stall rows."""
    await delete_stall(db, stall_id)
    if location_id:
        ctx = await _stall_context(db, location_id)
        return templates.TemplateResponse(
            request,
            "settings/partials/stall_rows.html",
            ctx,
        )
    return HTMLResponse("")


@router.post("/settings/locations/{location_id}/stalls/prefill", response_class=HTMLResponse)
async def prefill_stalls(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Pre-fill stalls from network charger templates (non-destructive)."""
    # Look up the location's network name
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    location = result.scalar_one_or_none()
    if location and location.network_id:
        net_result = await db.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == location.network_id)
        )
        network = net_result.scalar_one_or_none()
        if network:
            all_templates = await get_charger_templates(db)
            network_templates = all_templates.get(network.network_name, [])
            for tmpl in network_templates:
                await create_stall(
                    db,
                    location_id=location_id,
                    label=tmpl.get("label", "Charger"),
                    charger_type=tmpl.get("charger_type"),
                    rated_kw=tmpl.get("rated_kw"),
                    voltage=tmpl.get("voltage"),
                    amperage=tmpl.get("amperage"),
                    connector_type=tmpl.get("connector_type"),
                    is_default=False,
                )
    ctx = await _stall_context(db, location_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/stall_rows.html",
        ctx,
    )


# ---------------------------------------------------------------------------
# Subscription CRUD routes
# ---------------------------------------------------------------------------


async def _subscription_tab_context(db: AsyncSession, network_id: int) -> dict:
    """Build context for subscription_tab.html partial."""
    periods = await get_subscriptions_for_network(db, network_id)
    return {"periods": periods, "network_id": network_id}


@router.get("/settings/networks/{network_id}/subscriptions", response_class=HTMLResponse)
async def network_subscriptions(
    network_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Return subscription tab partial for a given network (lazy-loaded in modal)."""
    ctx = await _subscription_tab_context(db, network_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/subscription_tab.html",
        ctx,
    )


@router.post("/settings/networks/{network_id}/subscriptions", response_class=HTMLResponse)
async def create_subscription_route(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    member_rate: float = Form(...),
    monthly_fee: float = Form(0),
    start_date: str = Form(...),
    end_date: str = Form(""),
    notes: str = Form(""),
):
    """Create a new subscription period for a network."""
    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date.strip() else None

    try:
        await create_subscription(
            db,
            network_id=network_id,
            member_rate=member_rate,
            monthly_fee=monthly_fee,
            start_date=parsed_start,
            end_date=parsed_end,
            notes=notes.strip() or None,
        )
    except ValueError as e:
        ctx = await _subscription_tab_context(db, network_id)
        ctx["error"] = str(e)
        return templates.TemplateResponse(
            request,
            "settings/partials/subscription_tab.html",
            ctx,
        )

    ctx = await _subscription_tab_context(db, network_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/subscription_tab.html",
        ctx,
    )


@router.get("/settings/subscriptions/{subscription_id}/edit", response_class=HTMLResponse)
async def edit_subscription_form(
    subscription_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return inline edit form for a subscription period."""
    from sqlalchemy import select as sa_select

    from db.models.reference import EVNetworkSubscription

    result = await db.execute(
        sa_select(EVNetworkSubscription).where(EVNetworkSubscription.id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return HTMLResponse(status_code=404)

    return templates.TemplateResponse(
        request,
        "settings/partials/subscription_edit_row.html",
        {"sub": sub, "network_id": sub.network_id},
    )


@router.put("/settings/subscriptions/{subscription_id}", response_class=HTMLResponse)
async def update_subscription_route(
    subscription_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    member_rate: float = Form(...),
    monthly_fee: float = Form(0),
    start_date: str = Form(...),
    end_date: str = Form(""),
    notes: str = Form(""),
    network_id: int = Form(...),
):
    """Update a subscription period."""
    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date.strip() else None

    try:
        await update_subscription(
            db,
            subscription_id=subscription_id,
            member_rate=member_rate,
            monthly_fee=monthly_fee,
            start_date=parsed_start,
            end_date=parsed_end,
            notes=notes.strip() or None,
        )
    except ValueError as e:
        ctx = await _subscription_tab_context(db, network_id)
        ctx["error"] = str(e)
        return templates.TemplateResponse(
            request,
            "settings/partials/subscription_tab.html",
            ctx,
        )

    ctx = await _subscription_tab_context(db, network_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/subscription_tab.html",
        ctx,
    )


@router.delete("/settings/subscriptions/{subscription_id}", response_class=HTMLResponse)
async def delete_subscription_route(
    subscription_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_id: int = 0,
):
    """Delete a subscription period and return refreshed list."""
    await delete_subscription(db, subscription_id)
    if network_id:
        ctx = await _subscription_tab_context(db, network_id)
        return templates.TemplateResponse(
            request,
            "settings/partials/subscription_tab.html",
            ctx,
        )
    return HTMLResponse("")


HASS_SETTINGS_KEYS = [
    "home_latitude",
    "home_longitude",
    "home_location_name",
]


# ---------------------------------------------------------------------------
# Data Sources tab — registry-driven per-source config cards
# ---------------------------------------------------------------------------


def _mask_token(token: str) -> str:
    """Return a masked rendering of a stored token: asterisks + last 8 chars."""
    if not token:
        return ""
    if len(token) > 8:
        return "*" * (len(token) - 8) + token[-8:]
    return token


def _last_event_timeago(adapter_module) -> str | None:
    """Return human-readable 'X min ago' string or None if cache empty."""
    from datetime import UTC, datetime

    cache = getattr(adapter_module, "_last_seen_raw", {})
    if not cache:
        return None
    try:
        most_recent_iso = max(entry["seen_at"] for entry in cache.values())
        most_recent_dt = datetime.fromisoformat(most_recent_iso)
    except (KeyError, ValueError, TypeError):
        return None
    delta = datetime.now(UTC) - most_recent_dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


async def _load_existing_config(db: AsyncSession, descriptor):
    """Load existing data_source_configs row for descriptor, return Pydantic instance or None.

    Returns None for both missing rows and rows whose stored config_json fails
    schema validation (e.g. the pre-WR-05 seed shape with empty ha_url/ha_token).
    Mirrors the defensive validation the GET handler does so the masked-token
    preprocess can safely skip when there's no usable existing token to compare.
    """
    result = await db.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == descriptor.source_name,
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    try:
        return descriptor.config_schema.model_validate(row.config_json)
    except ValidationError:
        return None


def _masked_token_for_config(config: object | None) -> str:
    if isinstance(config, HAFordpassConfig):
        return _mask_token(config.ha_token)
    return ""


async def _upsert_data_source_config(db: AsyncSession, descriptor, config) -> None:
    """Persist config: UPDATE the existing row, INSERT one if missing.

    Fresh installs can reach first-save with no existing config row, while
    migrated installs usually update the seeded row.
    """
    from datetime import UTC, datetime

    result = await db.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == descriptor.source_name,
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = DataSourceConfig(
            source_name=descriptor.source_name,
            instance_label="default",
            config_json=config.model_dump(),
            enabled=True,
        )
        db.add(row)
    else:
        row.config_json = config.model_dump()
        row.updated_at = datetime.now(UTC)
    await db.commit()


def _card_health_inputs(descriptor) -> tuple[dict, str | None]:
    """Return (health_dict, last_seen_str) for the badge — only ha_fordpass today."""
    if descriptor.source_name == "ha_fordpass":
        rt = supervisor.get_runtime("ha_fordpass", "default")
        health = rt.health if rt is not None else {}
        return health, _last_event_timeago(ha_fordpass_adapter)
    return {}, None


@router.get("/settings/data-sources", response_class=HTMLResponse)
async def data_sources_tab(
    request: Request,
    db: AsyncSession = Depends(get_db),
    saved: bool = False,
):
    """Render the registry-driven Data Sources tab partial."""
    # ha_gas_price is folded into the unified Home Assistant card (rendered
    # for ha_fordpass) — its sensor-entity-id inputs save to app_settings via
    # the FordPass save handler, so we skip rendering it as its own card.
    cards = []
    for descriptor in SOURCE_REGISTRY:
        if descriptor.source_name == "ha_gas_price":
            continue
        result = await db.execute(
            select(DataSourceConfig).where(
                DataSourceConfig.source_name == descriptor.source_name,
                DataSourceConfig.instance_label == "default",
            )
        )
        row = result.scalar_one_or_none()
        config = None
        partial_config = None
        if row is not None:
            try:
                config = descriptor.config_schema.model_validate(row.config_json)
            except ValidationError:
                # Seed rows ship with empty ha_url/ha_token until the operator
                # configures the source. Surface stored values into the form
                # without exposing them as a fully-validated config object.
                partial_config = row.config_json or {}
        health, last_seen = _card_health_inputs(descriptor)
        card_ctx = {
            "descriptor": descriptor,
            "config": config,
            "partial_config": partial_config,
            "masked_token": _masked_token_for_config(config),
            "health": health,
            "last_seen": last_seen,
        }
        if descriptor.source_name == "ha_fordpass":
            gas_settings = await get_app_settings_dict(
                db,
                ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"],
            )
            card_ctx["gas_sensor_station_entity_id"] = gas_settings.get(
                "gas_sensor_station_entity_id", ""
            )
            card_ctx["gas_sensor_average_entity_id"] = gas_settings.get(
                "gas_sensor_average_entity_id", ""
            )
        cards.append(card_ctx)

    return templates.TemplateResponse(
        request,
        "settings/partials/data_sources_tab.html",
        {"cards": cards, "saved": saved},
    )


@router.post("/settings/data-sources/{source_name}", response_class=HTMLResponse)
async def save_data_source(
    source_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save per-source config; one handler covers the whole registry."""
    descriptor = next(
        (d for d in SOURCE_REGISTRY if d.source_name == source_name), None
    )
    if descriptor is None:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_name}")

    form: dict[str, Any] = dict(await request.form())

    # Masked-token preprocess — preserves the regression-locked invariant: if
    # the user submits the masked placeholder, do NOT overwrite the stored
    # token. Apply this BEFORE model_validate so the masked string never
    # becomes the persisted value. We compare against the rendered mask of
    # the stored token (asterisks + last 8 chars) so any other input —
    # including a real token starting with `*` — is treated as a legitimate
    # update.
    if "ha_token" in form:
        existing = await _load_existing_config(db, descriptor)
        if (
            isinstance(existing, HAFordpassConfig)
            and form["ha_token"] == _mask_token(existing.ha_token)
        ):
            form["ha_token"] = existing.ha_token

    # Boolean coercion for HTML checkbox: unchecked → absent from form,
    # checked → "true" or "on" (browser-dependent).
    form["ha_auto_connect"] = form.get("ha_auto_connect", "") in ("true", "on", "1")

    # Empty optional string → None (Pydantic optional)
    if form.get("ha_vin_override", "") == "":
        form["ha_vin_override"] = None

    # Pull the gas-sensor entity-id fields off the form before HAFordpassConfig
    # validation — they belong to ha_gas_price's config schema (today stored in
    # app_settings) and would fail HAFordpassConfig validation if left in.
    gas_station_entity = str(form.pop("gas_sensor_station_entity_id", "") or "")
    gas_average_entity = str(form.pop("gas_sensor_average_entity_id", "") or "")

    try:
        config = descriptor.config_schema.model_validate(form)
    except ValidationError as e:
        health, last_seen = _card_health_inputs(descriptor)
        return templates.TemplateResponse(
            request,
            "settings/partials/data_source_card.html",
            {
                "card": {
                    "descriptor": descriptor,
                    "config": None,
                    "partial_config": form,
                    "masked_token": form.get("ha_token", ""),
                    "health": health,
                    "last_seen": last_seen,
                    "gas_sensor_station_entity_id": gas_station_entity,
                    "gas_sensor_average_entity_id": gas_average_entity,
                },
                "errors": e.errors(),
            },
            status_code=422,
        )

    await _upsert_data_source_config(db, descriptor, config)

    if source_name == "ha_fordpass":
        await set_app_setting(db, "gas_sensor_station_entity_id", gas_station_entity)
        await set_app_setting(db, "gas_sensor_average_entity_id", gas_average_entity)
        from web.services.sources.ha_gas_price.adapter import (
            invalidate_gas_sensor_cache,
        )
        invalidate_gas_sensor_cache()

    health, last_seen = _card_health_inputs(descriptor)
    return templates.TemplateResponse(
        request,
        "settings/partials/data_source_card.html",
        {
            "card": {
                "descriptor": descriptor,
                "config": config,
                "partial_config": None,
                "masked_token": _masked_token_for_config(config),
                "health": health,
                "last_seen": last_seen,
                "gas_sensor_station_entity_id": gas_station_entity,
                "gas_sensor_average_entity_id": gas_average_entity,
            },
            "saved": True,
        },
    )


@router.get("/settings/hass/status", response_class=HTMLResponse)
async def hass_status(request: Request):
    """Return HASS connection status partial for polling."""
    runtime = supervisor.get_runtime("ha_fordpass", "default")
    health = runtime.health if runtime is not None else {}
    detected_vin = getattr(runtime, "detected_vin", None) if runtime is not None else None
    ha_config = getattr(runtime, "_ha_config", None) if runtime is not None else None
    unit_system = None
    if ha_config and "unit_system" in ha_config:
        unit_system = ha_config["unit_system"]
    return templates.TemplateResponse(
        request,
        "settings/partials/hass_status.html",
        {
            "health": health,
            "detected_vin": detected_vin,
            "unit_system": unit_system,
        },
    )


@router.post("/settings/hass/reconnect", response_class=HTMLResponse)
async def hass_reconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Stop and restart the HASS websocket runtime via the supervisor.

    Looks up the ha_fordpass:default config row, asks the supervisor to
    restart that runtime in place. The supervisor stops the existing runtime
    (if any), re-reads config_json from the row, and spawns a fresh runtime.
    """
    result = await db.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await supervisor.restart_runtime(row.id)
    runtime = supervisor.get_runtime("ha_fordpass", "default")
    health = runtime.health if runtime is not None else {}
    detected_vin = getattr(runtime, "detected_vin", None) if runtime is not None else None
    ha_config = getattr(runtime, "_ha_config", None) if runtime is not None else None
    unit_system = None
    if ha_config and "unit_system" in ha_config:
        unit_system = ha_config["unit_system"]
    return templates.TemplateResponse(
        request,
        "settings/partials/hass_status.html",
        {
            "health": health,
            "detected_vin": detected_vin,
            "unit_system": unit_system,
        },
    )


@router.post("/settings/hass/backfill", response_class=HTMLResponse)
async def hass_backfill(request: Request):
    """Trigger history backfill from HA REST API.

    Pulls as much charging session and gas sensor history as HA will return
    (no artificial time cap). Duplicates are automatically skipped.
    """
    runtime = supervisor.get_runtime("ha_fordpass", "default")
    if runtime is None or not runtime.health.get("connected"):
        return HTMLResponse(
            '<div class="alert alert-error text-sm">Must be connected to HA to backfill.</div>'
        )

    result = await cast(Any, runtime).backfill_history(days=None)

    if result.get("error"):
        return HTMLResponse(
            f'<div class="alert alert-error text-sm">{result["error"]}</div>'
        )

    sessions = result.get("sessions", {})
    gas = result.get("gas", {})

    parts: list[str] = [
        f"<strong>Sessions:</strong> {sessions.get('processed', 0)} processed"
    ]
    if sessions.get("errors"):
        parts[-1] += f", {sessions['errors']} errors"

    if gas:
        for entity_id, counts in gas.items():
            line = (
                f"<strong>{entity_id}:</strong> "
                f"{counts.get('inserted', 0)} new, "
                f"{counts.get('skipped', 0)} skipped"
            )
            if counts.get("errors"):
                line += f", {counts['errors']} errors"
            parts.append(line)
    else:
        parts.append("<em class='text-base-content/50'>No gas sensors configured</em>")

    body = "<br>".join(parts)
    return HTMLResponse(
        f'<div class="alert alert-success text-sm"><div>{body}<br>'
        f'<span class="text-xs text-base-content/60">Duplicates are automatically skipped.</span>'
        f'</div></div>'
    )


@router.post("/settings/hass/disconnect", response_class=HTMLResponse)
async def hass_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Stop the HASS websocket runtime via the supervisor."""
    result = await db.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await supervisor.stop_runtime(row.id)
    # Final health snapshot — the runtime may already be gone.
    runtime = supervisor.get_runtime("ha_fordpass", "default")
    health = (
        runtime.health
        if runtime is not None
        else {"connection_state": "disconnected", "connected": False}
    )
    return templates.TemplateResponse(
        request,
        "settings/partials/hass_status.html",
        {
            "health": health,
            "detected_vin": None,
            "unit_system": None,
        },
    )


async def _gas_price_history_context(db: AsyncSession) -> dict:
    """Build context for gas_price_history.html partial.

    Storage is metric ($/L). This builder pre-formats display values for the
    template so the cell-rendering loop reads `price.station_price_display`
    (already in user-display units, e.g. $/gal in US locale).
    """
    from datetime import datetime

    rows = await get_all_gas_prices(db)
    sensor_keys = ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"]
    sensor_settings = await get_app_settings_dict(db, sensor_keys)
    unit_ctx = await get_unit_context(db)
    distance_unit = unit_ctx["distance_unit"]
    gas_prices = []
    for r in rows:
        station_metric = float(r.station_price) if r.station_price is not None else None
        average_metric = float(r.average_price) if r.average_price is not None else None
        gas_prices.append({
            "id": r.id,
            "year": r.year,
            "month": r.month,
            "source": r.source,
            "station_price_metric": station_metric,
            "station_price_display": convert_price_per_volume(station_metric, distance_unit),
            "average_price_metric": average_metric,
            "average_price_display": convert_price_per_volume(average_metric, distance_unit),
        })
    return {
        **unit_ctx,
        "gas_prices": gas_prices,
        "sensor_settings": sensor_settings,
        "now": datetime.now(),
    }


@router.get("/settings/gas-prices", response_class=HTMLResponse)
async def gas_price_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the gas price history partial (lazy-loaded from gas_settings.html)."""
    ctx = await _gas_price_history_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/gas_price_history.html",
        ctx,
    )


@router.get("/settings/fuel-price-trend", response_class=HTMLResponse)
async def fuel_price_trend_chart(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return Plotly HTML for the Fuel Price Trend chart card.

    Lazy-loaded via HTMX from settings/partials/fuel_tab.html on Fuel tab activation.
    Reads metric storage; converts to user-display units before plotting.
    """
    from web.queries.gas_prices import build_fuel_price_trend_chart

    unit_ctx = await get_unit_context(db)
    chart_html = await build_fuel_price_trend_chart(db, unit_ctx["distance_unit"])
    return HTMLResponse(content=chart_html)


@router.post("/settings/gas-prices", response_class=HTMLResponse)
async def add_gas_price(
    request: Request,
    db: AsyncSession = Depends(get_db),
    year: int = Form(...),
    month: int = Form(...),
    station_price: float | None = Form(None),
    average_price: float | None = Form(None),
):
    """Add or update a gas price entry. Form values in display units; stored as $/L."""
    unit_ctx = await get_unit_context(db)
    station_metric = to_metric_price_per_volume(station_price, unit_ctx["distance_unit"])
    average_metric = to_metric_price_per_volume(average_price, unit_ctx["distance_unit"])
    saved_row = await upsert_gas_price(
        db, year, month, station_price=station_metric, average_price=average_metric
    )
    ctx = await _gas_price_history_context(db)
    ctx["saved"] = True
    ctx["just_saved_row_id"] = getattr(saved_row, "id", None)
    return templates.TemplateResponse(
        request,
        "settings/partials/gas_price_history.html",
        ctx,
    )


@router.delete("/settings/gas-prices/{price_id}", response_class=HTMLResponse)
async def delete_gas_price_route(
    price_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await delete_gas_price(db, price_id)
    ctx = await _gas_price_history_context(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/gas_price_history.html",
        ctx,
    )


async def _hass_gas_sensors_context(
    db: AsyncSession, *, check_live: bool = False, gas_saved: bool = False
) -> dict:
    """Build context for the HASS-page gas sensor partial.

    When check_live is True, queries HA for the current state of each
    configured sensor via the REST client so the UI can show "not reporting"
    vs the live value. Otherwise returns None for sensor_states so the
    template just reflects the stored config and DB stats.
    """
    from sqlalchemy import func as sa_func

    from db.models.reference import GasPriceHistory, GasPriceReading

    sensor_keys = ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"]
    sensor_settings = await get_app_settings_dict(db, sensor_keys)

    sensor_states: dict = {"station": None, "average": None}
    if check_live:
        runtime = supervisor.get_runtime("ha_fordpass", "default")
        for role, key in (
            ("station", "gas_sensor_station_entity_id"),
            ("average", "gas_sensor_average_entity_id"),
        ):
            entity_id = (sensor_settings.get(key) or "").strip()
            if not entity_id:
                continue
            if runtime is None:
                continue
            state_obj = await cast(Any, runtime).fetch_entity_state(entity_id)
            if not state_obj:
                continue
            raw_val = state_obj.get("state")
            try:
                price = float(str(raw_val)) if raw_val not in (None, "unknown", "unavailable", "") else None
            except (TypeError, ValueError):
                price = None
            if price is None:
                continue
            last_changed = state_obj.get("last_changed") or state_obj.get("last_updated") or ""
            sensor_states[role] = {
                "value": f"${price:.3f}",
                "last_changed": last_changed[:19].replace("T", " ") if last_changed else "",
            }

    # DB stats: total readings + latest timestamp + monthly row count
    reading_count_stmt = select(sa_func.count()).select_from(GasPriceReading)
    latest_reading_stmt = select(sa_func.max(GasPriceReading.recorded_at))
    monthly_count_stmt = select(sa_func.count()).select_from(GasPriceHistory)

    reading_count = (await db.execute(reading_count_stmt)).scalar_one() or 0
    latest_reading_ts = (await db.execute(latest_reading_stmt)).scalar_one()
    monthly_count = (await db.execute(monthly_count_stmt)).scalar_one() or 0

    latest_reading = ""
    if latest_reading_ts:
        latest_reading = latest_reading_ts.strftime("%Y-%m-%d %H:%M")

    return {
        "sensor_settings": sensor_settings,
        "sensor_states": sensor_states,
        "db_stats": {
            "reading_count": reading_count,
            "latest_reading": latest_reading,
            "monthly_count": monthly_count,
        },
        "gas_saved": gas_saved,
    }


@router.get("/settings/hass/gas-sensors", response_class=HTMLResponse)
async def hass_gas_sensors_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the gas-sensor card for the HASS page, with a live HA check."""
    ctx = await _hass_gas_sensors_context(db, check_live=True)
    return templates.TemplateResponse(
        request, "settings/partials/hass_gas_sensors.html", ctx
    )


@router.post("/settings/hass/gas-sensors", response_class=HTMLResponse)
async def save_hass_gas_sensors(
    request: Request,
    db: AsyncSession = Depends(get_db),
    gas_sensor_station_entity_id: str | None = Form(None),
    gas_sensor_average_entity_id: str | None = Form(None),
):
    """Save gas sensor config + trigger an immediate live check in the render."""
    await set_app_setting(
        db, "gas_sensor_station_entity_id", gas_sensor_station_entity_id or ""
    )
    await set_app_setting(
        db, "gas_sensor_average_entity_id", gas_sensor_average_entity_id or ""
    )
    from web.services.sources.ha_gas_price.adapter import invalidate_gas_sensor_cache
    invalidate_gas_sensor_cache()
    ctx = await _hass_gas_sensors_context(db, check_live=True, gas_saved=True)
    return templates.TemplateResponse(
        request, "settings/partials/hass_gas_sensors.html", ctx
    )


# Legacy route kept for compatibility with any pre-existing HTMX calls from
# the fuel tab. The new form lives on the HASS page; this still works for
# anything that posted to the old URL.
@router.post("/settings/gas-sensors", response_class=HTMLResponse)
async def save_gas_sensors(
    request: Request,
    db: AsyncSession = Depends(get_db),
    gas_sensor_station_entity_id: str | None = Form(None),
    gas_sensor_average_entity_id: str | None = Form(None),
):
    await set_app_setting(db, "gas_sensor_station_entity_id", gas_sensor_station_entity_id or "")
    await set_app_setting(db, "gas_sensor_average_entity_id", gas_sensor_average_entity_id or "")
    from web.services.sources.ha_gas_price.adapter import invalidate_gas_sensor_cache
    invalidate_gas_sensor_cache()
    ctx = await _gas_price_history_context(db)
    ctx["saved"] = True
    return templates.TemplateResponse(
        request,
        "settings/partials/gas_price_history.html",
        ctx,
    )


@router.post("/settings/units", response_class=HTMLResponse)
async def update_unit_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    distance_unit: str = Form("us"),
    temp_unit: str = Form("us"),
):
    # Validate: only "us" or "metric" accepted for each axis
    if distance_unit not in ("us", "metric"):
        distance_unit = "us"
    if temp_unit not in ("us", "metric"):
        temp_unit = "us"
    await set_app_setting(db, "distance_unit", distance_unit)
    await set_app_setting(db, "temp_unit", temp_unit)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    response = templates.TemplateResponse(
        request,
        "settings/partials/unit_settings.html",
        {"settings": settings, "saved": True},
    )
    # Unit changes affect every page — force a full client-side refresh so
    # cached partials (Fuel tab, Costs page, charts) re-render in new units.
    response.headers["HX-Refresh"] = "true"
    return response


@router.post("/settings/timezone", response_class=HTMLResponse)
async def update_timezone_setting(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_timezone: str = Form("UTC"),
):
    """Save the user's preferred display timezone."""
    await set_app_setting(db, "user_timezone", user_timezone)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    response = templates.TemplateResponse(
        request,
        "settings/partials/timezone_settings.html",
        {"settings": settings, "saved": True},
    )
    # Timezone affects every timestamp on every page — full refresh.
    response.headers["HX-Refresh"] = "true"
    return response


@router.post("/settings/toggles", response_class=HTMLResponse)
async def update_toggles(
    request: Request,
    db: AsyncSession = Depends(get_db),
    comparison_gas_enabled: str | None = Form(None),
    comparison_network_enabled: str | None = Form(None),
    comparison_section_visible: str | None = Form(None),
):
    await set_app_setting(
        db,
        "comparison_gas_enabled",
        "true" if comparison_gas_enabled is not None else "false",
    )
    await set_app_setting(
        db,
        "comparison_network_enabled",
        "true" if comparison_network_enabled is not None else "false",
    )
    await set_app_setting(
        db,
        "comparison_section_visible",
        "true" if comparison_section_visible is not None else "false",
    )
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    return templates.TemplateResponse(
        request,
        "settings/partials/gas_settings.html",
        {"settings": settings, "saved": True},
    )


@router.post("/settings/developer-mode", response_class=HTMLResponse)
async def update_developer_mode(
    request: Request,
    db: AsyncSession = Depends(get_db),
    developer_mode: str | None = Form(None),
):
    enabled = developer_mode is not None
    await set_app_setting(db, "developer_mode", "true" if enabled else "false")
    dev_mode_module.set_enabled(enabled)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    response = templates.TemplateResponse(
        request,
        "settings/partials/developer_settings.html",
        {"settings": settings, "saved": True},
    )
    # Toggling developer mode adds/removes nav items — force full refresh.
    response.headers["HX-Refresh"] = "true"
    return response
