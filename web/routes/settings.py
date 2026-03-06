from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork, EVLocationLookup
from web.dependencies import get_db
from web.queries.settings import (
    create_location,
    create_network,
    delete_location,
    delete_network,
    get_all_networks,
    get_app_settings_dict,
    get_locations_for_network,
    set_app_setting,
    update_location,
    update_network,
)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def _network_management_context(db: AsyncSession) -> dict:
    """Build context dict for network_management.html — networks + per-network location counts."""
    networks = await get_all_networks(db)
    loc_count_result = await db.execute(
        select(EVLocationLookup.network_id, func.count().label("cnt"))
        .group_by(EVLocationLookup.network_id)
    )
    location_counts = {row.network_id: row.cnt for row in loc_count_result.all()}
    return {"networks": networks, "location_counts": location_counts}


SETTINGS_KEYS = [
    "gas_price_per_gallon",
    "vehicle_mpg",
    "comparison_gas_enabled",
    "comparison_network_enabled",
    "comparison_section_visible",
    "efficiency_unit",
]


@router.get("/settings", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tab: Optional[str] = Query(None),
):
    net_ctx = await _network_management_context(db)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    if tab == "import":
        active_tab = "import"
    elif tab == "networks":
        active_tab = "networks"
    else:
        active_tab = "general"
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            **net_ctx,
            "settings": settings,
            "active_page": "settings",
            "page_title": "Settings",
            "active_tab": active_tab,
        },
    )


@router.post("/settings/networks", response_class=HTMLResponse)
async def create_network_route(
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_name: str = Form(...),
    cost_per_kwh: Optional[float] = Form(None),
    color: Optional[str] = Form(None),
    is_free: Optional[str] = Form(None),
):
    is_free_bool = is_free is not None
    await create_network(
        db,
        name=network_name,
        cost_per_kwh=cost_per_kwh,
        is_free=is_free_bool,
        color=color,
    )
    net_ctx = await _network_management_context(db)
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
    cost_per_kwh: Optional[float] = Form(None),
    color: Optional[str] = Form(None),
    is_free: Optional[str] = Form(None),
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

    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.network_id == network_id)
    )
    sessions = result.scalars().all()

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
    location_type: Optional[str] = Form(None),
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
    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.network_id == network_id)
    )
    sessions = result.scalars().all()
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
    """Return location rows partial for a given network."""
    locations = await get_locations_for_network(db, network_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/location_rows.html",
        {"locations": locations, "network_id": network_id},
    )


@router.post("/settings/networks/{network_id}/locations", response_class=HTMLResponse)
async def create_location_route(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_name: str = Form(...),
    location_type: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    cost_per_kwh: Optional[float] = Form(None),
):
    """Add a location under a network."""
    await create_location(
        db, network_id, location_name, location_type, notes,
        address=address or None, latitude=latitude, longitude=longitude,
        cost_per_kwh=cost_per_kwh,
    )
    locations = await get_locations_for_network(db, network_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/location_rows.html",
        {"locations": locations, "network_id": network_id},
    )


@router.put("/settings/locations/{location_id}", response_class=HTMLResponse)
async def update_location_route(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_name: str = Form(...),
    location_type: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    network_id: int = Form(...),
    address: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    cost_per_kwh: Optional[float] = Form(None),
):
    """Update a location and return the refreshed location list."""
    await update_location(
        db, location_id, location_name, location_type, notes,
        address=address or None, latitude=latitude, longitude=longitude,
        cost_per_kwh=cost_per_kwh,
    )
    locations = await get_locations_for_network(db, network_id)
    return templates.TemplateResponse(
        request,
        "settings/partials/location_rows.html",
        {"locations": locations, "network_id": network_id},
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
        return templates.TemplateResponse(
            request,
            "settings/partials/location_rows.html",
            {"locations": locations, "network_id": network_id},
        )
    return HTMLResponse("")


@router.post("/settings/gas", response_class=HTMLResponse)
async def update_gas_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    gas_price: float = Form(...),
    vehicle_mpg: float = Form(...),
):
    await set_app_setting(db, "gas_price_per_gallon", str(gas_price))
    await set_app_setting(db, "vehicle_mpg", str(vehicle_mpg))
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    return templates.TemplateResponse(
        request,
        "settings/partials/gas_settings.html",
        {"settings": settings, "saved": True},
    )


@router.post("/settings/units", response_class=HTMLResponse)
async def update_unit_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    efficiency_unit: str = Form("us"),
):
    # Validate: only "us" or "eu" accepted
    if efficiency_unit not in ("us", "eu"):
        efficiency_unit = "us"
    await set_app_setting(db, "efficiency_unit", efficiency_unit)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    return templates.TemplateResponse(
        request,
        "settings/partials/unit_settings.html",
        {"settings": settings, "saved": True},
    )


@router.post("/settings/toggles", response_class=HTMLResponse)
async def update_toggles(
    request: Request,
    db: AsyncSession = Depends(get_db),
    comparison_gas_enabled: Optional[str] = Form(None),
    comparison_network_enabled: Optional[str] = Form(None),
    comparison_section_visible: Optional[str] = Form(None),
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
