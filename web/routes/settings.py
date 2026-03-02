from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

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
    networks = await get_all_networks(db)
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
            "networks": networks,
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
    networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        {"networks": networks},
    )


@router.get("/settings/networks", response_class=HTMLResponse)
async def networks_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the network management partial (used by cancel button to revert edits)."""
    networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        {"networks": networks},
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
    networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        {"networks": networks},
    )


@router.delete("/settings/networks/{network_id}", response_class=HTMLResponse)
async def delete_network_route(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await delete_network(db, network_id=network_id)
    networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/network_management.html",
        {"networks": networks},
    )


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
):
    """Add a location under a network."""
    await create_location(db, network_id, location_name, location_type, notes)
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
):
    """Update a location and return the refreshed location list."""
    await update_location(db, location_id, location_name, location_type, notes)
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
