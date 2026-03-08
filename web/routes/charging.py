from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork, EVLocationLookup
from web.dependencies import get_db
from web.queries.settings import get_all_networks

router = APIRouter(prefix="/charging")
templates = Jinja2Templates(directory="web/templates")


async def _review_context(db: AsyncSession) -> dict:
    """Build context for the review queue page."""
    # Unverified locations
    loc_result = await db.execute(
        select(EVLocationLookup)
        .where(EVLocationLookup.is_verified == False)  # noqa: E712
        .order_by(EVLocationLookup.id.desc())
    )
    unverified_locations = list(loc_result.scalars().all())

    # Unverified networks
    net_result = await db.execute(
        select(EVChargingNetwork)
        .where(EVChargingNetwork.is_verified == False)  # noqa: E712
        .order_by(EVChargingNetwork.id.desc())
    )
    unverified_networks = list(net_result.scalars().all())

    # Session counts per location
    loc_session_counts: dict[int, int] = {}
    if unverified_locations:
        loc_ids = [loc.id for loc in unverified_locations]
        result = await db.execute(
            select(EVChargingSession.location_id, func.count().label("cnt"))
            .where(EVChargingSession.location_id.in_(loc_ids))
            .group_by(EVChargingSession.location_id)
        )
        loc_session_counts = {row.location_id: row.cnt for row in result.all()}

    # Session counts per network
    net_session_counts: dict[int, int] = {}
    if unverified_networks:
        net_ids = [n.id for n in unverified_networks]
        result = await db.execute(
            select(EVChargingSession.network_id, func.count().label("cnt"))
            .where(EVChargingSession.network_id.in_(net_ids))
            .group_by(EVChargingSession.network_id)
        )
        net_session_counts = {row.network_id: row.cnt for row in result.all()}

    # All networks for edit dropdown
    all_networks = await get_all_networks(db)

    return {
        "unverified_locations": unverified_locations,
        "unverified_networks": unverified_networks,
        "loc_session_counts": loc_session_counts,
        "net_session_counts": net_session_counts,
        "all_networks": all_networks,
    }


@router.get("/review", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Review queue page for unverified networks and locations."""
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/review_queue.html",
        {
            **ctx,
            "active_page": "review_queue",
            "page_title": "Review Queue",
        },
    )


@router.get("/review/table", response_class=HTMLResponse)
async def review_table(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the review table partial (used to refresh after network modal actions)."""
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/partials/review_table.html",
        ctx,
    )


@router.post("/review/location/{location_id}/verify", response_class=HTMLResponse)
async def verify_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark a location as verified."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc:
        loc.is_verified = True
        loc.source_system = "manual"
        await db.commit()
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/partials/review_table.html",
        ctx,
    )


@router.post("/review/network/{network_id}/verify", response_class=HTMLResponse)
async def verify_network(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark a network as verified."""
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    net = result.scalar_one_or_none()
    if net:
        net.is_verified = True
        net.source_system = "manual"
        await db.commit()
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/partials/review_table.html",
        ctx,
    )


@router.post("/review/location/{location_id}/edit", response_class=HTMLResponse)
async def edit_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_name: str = Form(...),
    address: Optional[str] = Form(None),
    location_type: Optional[str] = Form(None),
    network_id: Optional[int] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    cost_per_kwh: Optional[float] = Form(None),
):
    """Edit an unverified location."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc:
        loc.location_name = location_name
        loc.address = address or None
        loc.location_type = location_type or None
        loc.network_id = network_id or None
        loc.latitude = latitude
        loc.longitude = longitude
        loc.cost_per_kwh = cost_per_kwh
        await db.commit()
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/partials/review_table.html",
        ctx,
    )


@router.post("/review/location/{location_id}/delete", response_class=HTMLResponse)
async def delete_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an unverified location (safety check: only if is_verified=False)."""
    result = await db.execute(
        select(EVLocationLookup).where(
            EVLocationLookup.id == location_id,
            EVLocationLookup.is_verified == False,  # noqa: E712
        )
    )
    loc = result.scalar_one_or_none()
    if loc:
        await db.delete(loc)
        await db.commit()
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/partials/review_table.html",
        ctx,
    )


@router.post("/review/network/{network_id}/delete", response_class=HTMLResponse)
async def delete_network(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an unverified network (safety check: only if is_verified=False)."""
    result = await db.execute(
        select(EVChargingNetwork).where(
            EVChargingNetwork.id == network_id,
            EVChargingNetwork.is_verified == False,  # noqa: E712
        )
    )
    net = result.scalar_one_or_none()
    if net:
        await db.delete(net)
        await db.commit()
    ctx = await _review_context(db)
    return templates.TemplateResponse(
        request,
        "charging/partials/review_table.html",
        ctx,
    )
