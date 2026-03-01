from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.settings import (
    get_all_networks,
    get_app_settings_dict,
    set_app_setting,
    upsert_network,
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
):
    networks = await get_all_networks(db)
    settings = await get_app_settings_dict(db, SETTINGS_KEYS)
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            "networks": networks,
            "settings": settings,
            "active_page": "settings",
            "page_title": "Settings",
        },
    )


@router.post("/settings/networks", response_class=HTMLResponse)
async def update_network(
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_id: int = Form(...),
    cost_per_kwh: float = Form(...),
    is_free: Optional[str] = Form(None),
):
    # HTML checkbox sends "on" when checked, nothing when unchecked
    is_free_bool = is_free is not None
    await upsert_network(db, network_id, cost_per_kwh, is_free_bool)
    networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/networks_table.html",
        {"networks": networks, "saved": True},
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
