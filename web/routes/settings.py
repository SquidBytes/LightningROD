from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargerStall, EVChargingNetwork, EVLocationLookup
from web.dependencies import get_db
from web.queries.settings import (
    create_location,
    create_network,
    create_stall,
    delete_location,
    delete_network,
    delete_stall,
    get_all_networks,
    get_app_setting,
    get_app_settings_dict,
    get_charger_templates,
    get_locations_for_network,
    get_stalls_for_location,
    set_app_setting,
    update_location,
    update_network,
    update_stall,
)
from web.services.csv_parser import get_db_field_options

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


SETTINGS_KEYS = [
    "gas_price_per_gallon",
    "vehicle_mpg",
    "comparison_gas_enabled",
    "comparison_network_enabled",
    "comparison_section_visible",
    "efficiency_unit",
    "user_timezone",
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
    elif tab == "hass":
        active_tab = "hass"
    else:
        active_tab = "general"

    # Import tab needs extra context for template features and timezone selector
    import_ctx: dict = {}
    if active_tab == "import":
        user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
        import_ctx = {"db_fields": get_db_field_options(), "user_tz": user_tz}

    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            **net_ctx,
            **import_ctx,
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


@router.post("/settings/locations/{location_id}/stalls", response_class=HTMLResponse)
async def create_stall_route(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    stall_label: str = Form(...),
    charger_type: Optional[str] = Form(None),
    rated_kw: Optional[float] = Form(None),
    voltage: Optional[float] = Form(None),
    amperage: Optional[float] = Form(None),
    connector_type: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    is_default: Optional[str] = Form(None),
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
    charger_type: Optional[str] = Form(None),
    rated_kw: Optional[float] = Form(None),
    voltage: Optional[float] = Form(None),
    amperage: Optional[float] = Form(None),
    connector_type: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    is_default: Optional[str] = Form(None),
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


HASS_SETTINGS_KEYS = [
    "ha_url",
    "ha_token",
    "ha_vin_override",
    "ha_unit_system",
    "ha_auto_connect",
    "home_latitude",
    "home_longitude",
    "home_location_name",
]


@router.get("/settings/hass", response_class=HTMLResponse)
async def hass_settings_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return HASS configuration partial with current values."""
    settings = await get_app_settings_dict(db, HASS_SETTINGS_KEYS)
    # Mask token for display: show only last 8 chars
    token = settings.get("ha_token", "")
    masked_token = ""
    if token:
        if len(token) > 8:
            masked_token = "*" * (len(token) - 8) + token[-8:]
        else:
            masked_token = token
    return templates.TemplateResponse(
        request,
        "settings/partials/hass_settings.html",
        {"hass": settings, "masked_token": masked_token},
    )


@router.post("/settings/hass", response_class=HTMLResponse)
async def save_hass_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    ha_url: str = Form(""),
    ha_token: str = Form(""),
    ha_vin_override: str = Form(""),
    ha_unit_system: str = Form("auto"),
    ha_auto_connect: Optional[str] = Form(None),
):
    """Save HASS configuration to app_settings."""
    if ha_url:
        # Strip trailing slash for consistency
        ha_url = ha_url.rstrip("/")
    await set_app_setting(db, "ha_url", ha_url)
    # Only overwrite token if a new value was provided (not the masked placeholder)
    if ha_token and not ha_token.startswith("*"):
        await set_app_setting(db, "ha_token", ha_token)
    await set_app_setting(db, "ha_vin_override", ha_vin_override)
    if ha_unit_system not in ("auto", "metric", "imperial"):
        ha_unit_system = "auto"
    await set_app_setting(db, "ha_unit_system", ha_unit_system)
    await set_app_setting(
        db, "ha_auto_connect", "true" if ha_auto_connect is not None else "false"
    )

    # Re-read saved values for display
    settings = await get_app_settings_dict(db, HASS_SETTINGS_KEYS)
    token = settings.get("ha_token", "")
    masked_token = ""
    if token:
        if len(token) > 8:
            masked_token = "*" * (len(token) - 8) + token[-8:]
        else:
            masked_token = token

    response = templates.TemplateResponse(
        request,
        "settings/partials/hass_settings.html",
        {"hass": settings, "masked_token": masked_token, "saved": True},
    )
    return response


@router.post("/settings/hass/home-location", response_class=HTMLResponse)
async def save_home_location_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    home_location_name: str = Form(""),
    home_latitude: str = Form(""),
    home_longitude: str = Form(""),
):
    """Save home location settings to app_settings."""
    await set_app_setting(db, "home_location_name", home_location_name)
    await set_app_setting(db, "home_latitude", home_latitude)
    await set_app_setting(db, "home_longitude", home_longitude)

    # Re-read saved values for display
    settings = await get_app_settings_dict(db, HASS_SETTINGS_KEYS)
    token = settings.get("ha_token", "")
    masked_token = ""
    if token:
        if len(token) > 8:
            masked_token = "*" * (len(token) - 8) + token[-8:]
        else:
            masked_token = token

    return templates.TemplateResponse(
        request,
        "settings/partials/hass_settings.html",
        {"hass": settings, "masked_token": masked_token, "saved": True},
    )


@router.get("/settings/hass/status", response_class=HTMLResponse)
async def hass_status(request: Request):
    """Return HASS connection status partial for polling."""
    from web.services.hass_client import hass_service

    health = hass_service.health
    detected_vin = getattr(hass_service, "detected_vin", None)
    ha_config = getattr(hass_service, "_ha_config", None)
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
async def hass_reconnect(request: Request):
    """Stop and restart the HASS websocket service."""
    from web.services.hass_client import hass_service, start_hass_service

    await hass_service.stop()
    await start_hass_service()
    health = hass_service.health
    detected_vin = getattr(hass_service, "detected_vin", None)
    ha_config = getattr(hass_service, "_ha_config", None)
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
    """Trigger history backfill from HA REST API for past charging sessions."""
    from web.services.hass_client import hass_service

    if not hass_service.health.get("connected"):
        return HTMLResponse(
            '<div class="alert alert-error text-sm">Must be connected to HA to backfill.</div>'
        )

    result = await hass_service.backfill_history(days=30)

    if result.get("error"):
        return HTMLResponse(
            f'<div class="alert alert-error text-sm">{result["error"]}</div>'
        )

    return HTMLResponse(
        f'<div class="alert alert-success text-sm">'
        f'Backfill complete: {result["processed"]} sessions processed'
        f'{", " + str(result["errors"]) + " errors" if result["errors"] else ""}. '
        f'Duplicates are automatically skipped.'
        f'</div>'
    )


@router.post("/settings/hass/disconnect", response_class=HTMLResponse)
async def hass_disconnect(request: Request):
    """Stop the HASS websocket service."""
    from web.services.hass_client import hass_service

    await hass_service.stop()
    health = hass_service.health
    return templates.TemplateResponse(
        request,
        "settings/partials/hass_status.html",
        {
            "health": health,
            "detected_vin": None,
            "unit_system": None,
        },
    )


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


@router.post("/settings/timezone", response_class=HTMLResponse)
async def update_timezone_setting(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_timezone: str = Form("UTC"),
):
    """Save the user's preferred display timezone."""
    await set_app_setting(db, "user_timezone", user_timezone)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    return templates.TemplateResponse(
        request,
        "settings/partials/timezone_settings.html",
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
