from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import (
    EVChargerStall,
    EVChargingNetwork,
    EVLocationGPSAlias,
    EVLocationLookup,
    EVNetworkNameAlias,
    EVNetworkSubscription,
)
from web.dependencies import get_db
from web.queries.settings import get_all_networks
from web.queries.vehicles import get_active_vehicle, get_all_vehicles

router = APIRouter(prefix="/review")
templates = Jinja2Templates(directory="web/templates")


# ---------------------------------------------------------------------------
# Query helpers for tab-based review queue
# ---------------------------------------------------------------------------


async def _networks_context(
    db: AsyncSession,
    q: Optional[str] = None,
    filter: str = "all",
    sort: str = "name",
) -> dict:
    """Build context for the networks tab with search, filter, sort."""
    # --- counts for sub-filter badges (always unfiltered by q) ---
    total_count_result = await db.execute(select(func.count()).select_from(EVChargingNetwork))
    total_all = total_count_result.scalar() or 0

    unverified_count_result = await db.execute(
        select(func.count())
        .select_from(EVChargingNetwork)
        .where(EVChargingNetwork.is_verified == False)  # noqa: E712
    )
    total_unverified = unverified_count_result.scalar() or 0
    total_verified = total_all - total_unverified

    filter_counts = {
        "all": total_all,
        "unverified": total_unverified,
        "verified": total_verified,
    }

    # --- session counts per network (subquery) ---
    session_count_sub = (
        select(func.count())
        .where(EVChargingSession.network_id == EVChargingNetwork.id)
        .correlate(EVChargingNetwork)
        .scalar_subquery()
        .label("session_count")
    )

    # --- main query ---
    stmt = select(EVChargingNetwork, session_count_sub)

    # filter
    if filter == "unverified":
        stmt = stmt.where(EVChargingNetwork.is_verified == False)  # noqa: E712
    elif filter == "verified":
        stmt = stmt.where(EVChargingNetwork.is_verified == True)  # noqa: E712

    # search
    if q and q.strip():
        stmt = stmt.where(func.lower(EVChargingNetwork.network_name).contains(q.strip().lower()))

    # sort
    if sort == "sessions":
        stmt = stmt.order_by(session_count_sub.desc(), EVChargingNetwork.network_name)
    elif sort == "status":
        stmt = stmt.order_by(EVChargingNetwork.is_verified.asc(), EVChargingNetwork.network_name)
    else:  # default: name
        stmt = stmt.order_by(EVChargingNetwork.network_name)

    result = await db.execute(stmt)
    rows = result.all()

    networks = []
    net_session_counts: dict[int, int] = {}
    for row in rows:
        net = row[0]
        count = row[1] or 0
        networks.append(net)
        net_session_counts[net.id] = count

    # All networks for merge target dropdowns
    all_networks = await get_all_networks(db)

    return {
        "networks": networks,
        "net_session_counts": net_session_counts,
        "all_networks": all_networks,
        "filter_counts": filter_counts,
        "active_filter": filter,
        "current_q": q or "",
        "current_sort": sort,
    }


async def _locations_context(
    db: AsyncSession,
    q: Optional[str] = None,
    filter: str = "all",
    sort: str = "name",
) -> dict:
    """Build context for the locations tab with search, filter, sort."""
    # --- counts for sub-filter badges ---
    total_count_result = await db.execute(select(func.count()).select_from(EVLocationLookup))
    total_all = total_count_result.scalar() or 0

    unverified_count_result = await db.execute(
        select(func.count())
        .select_from(EVLocationLookup)
        .where(EVLocationLookup.is_verified == False)  # noqa: E712
    )
    total_unverified = unverified_count_result.scalar() or 0
    total_verified = total_all - total_unverified

    filter_counts = {
        "all": total_all,
        "unverified": total_unverified,
        "verified": total_verified,
    }

    # --- session counts per location (subquery) ---
    session_count_sub = (
        select(func.count())
        .where(EVChargingSession.location_id == EVLocationLookup.id)
        .correlate(EVLocationLookup)
        .scalar_subquery()
        .label("session_count")
    )

    # --- main query ---
    stmt = select(EVLocationLookup, session_count_sub)

    # filter
    if filter == "unverified":
        stmt = stmt.where(EVLocationLookup.is_verified == False)  # noqa: E712
    elif filter == "verified":
        stmt = stmt.where(EVLocationLookup.is_verified == True)  # noqa: E712

    # search
    if q and q.strip():
        stmt = stmt.where(func.lower(EVLocationLookup.location_name).contains(q.strip().lower()))

    # sort
    if sort == "sessions":
        stmt = stmt.order_by(session_count_sub.desc(), EVLocationLookup.location_name)
    elif sort == "status":
        stmt = stmt.order_by(EVLocationLookup.is_verified.asc(), EVLocationLookup.location_name)
    else:  # default: name
        stmt = stmt.order_by(EVLocationLookup.location_name)

    result = await db.execute(stmt)
    rows = result.all()

    locations = []
    loc_session_counts: dict[int, int] = {}
    for row in rows:
        loc = row[0]
        count = row[1] or 0
        locations.append(loc)
        loc_session_counts[loc.id] = count

    # All networks for dropdowns (edit form network select, merge targets)
    all_networks = await get_all_networks(db)

    # All locations for merge target dropdown
    all_locs_result = await db.execute(
        select(EVLocationLookup).order_by(EVLocationLookup.location_name)
    )
    all_locations = list(all_locs_result.scalars().all())

    return {
        "locations": locations,
        "loc_session_counts": loc_session_counts,
        "all_networks": all_networks,
        "all_locations": all_locations,
        "filter_counts": filter_counts,
        "active_filter": filter,
        "current_q": q or "",
        "current_sort": sort,
    }


# ---------------------------------------------------------------------------
# Full page + tab partial endpoints
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    tab: str = "networks",
    q: Optional[str] = None,
    filter: str = "all",
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
):
    """Review queue page with Networks/Locations tabs."""
    # Validate tab
    if tab not in ("networks", "locations"):
        tab = "networks"

    # Build context for the active tab
    if tab == "networks":
        tab_ctx = await _networks_context(db, q=q, filter=filter, sort=sort)
    else:
        tab_ctx = await _locations_context(db, q=q, filter=filter, sort=sort)

    # Total counts for tab badges (always unfiltered)
    net_count_result = await db.execute(select(func.count()).select_from(EVChargingNetwork))
    network_count = net_count_result.scalar() or 0

    loc_count_result = await db.execute(select(func.count()).select_from(EVLocationLookup))
    location_count = loc_count_result.scalar() or 0

    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)

    return templates.TemplateResponse(
        request,
        "review/review_queue.html",
        {
            **tab_ctx,
            "active_tab": tab,
            "network_count": network_count,
            "location_count": location_count,
            "active_page": "review_queue",
            "page_title": "Review Queue",
            "active_vehicle": active_vehicle,
            "all_vehicles": all_vehicles,
        },
    )


@router.get("/networks", response_class=HTMLResponse)
async def review_networks(
    request: Request,
    q: Optional[str] = None,
    filter: str = "all",
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
):
    """Networks tab partial -- used by HTMX tab clicks and search/filter/sort."""
    ctx = await _networks_context(db, q=q, filter=filter, sort=sort)
    return templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )


@router.get("/locations", response_class=HTMLResponse)
async def review_locations(
    request: Request,
    q: Optional[str] = None,
    filter: str = "all",
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
):
    """Locations tab partial -- used by HTMX tab clicks and search/filter/sort."""
    ctx = await _locations_context(db, q=q, filter=filter, sort=sort)
    return templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )


# ---------------------------------------------------------------------------
# Action endpoints (verify, edit, delete)
# ---------------------------------------------------------------------------


@router.post("/location/{location_id}/verify", response_class=HTMLResponse)
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
    ctx = await _locations_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )


@router.post("/network/{network_id}/verify", response_class=HTMLResponse)
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
    ctx = await _networks_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )


@router.post("/location/{location_id}/edit", response_class=HTMLResponse)
async def edit_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_name: str = Form(...),
    address: str = Form(""),
    location_type: str = Form(""),
    network_id: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    cost_per_kwh: str = Form(""),
):
    """Edit a location. Accepts optional numeric fields as strings so empty
    form values coerce cleanly to None instead of tripping FastAPI's 422
    validation on `Optional[int/float]`."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc:
        loc.location_name = location_name
        loc.address = address.strip() or None
        loc.location_type = location_type.strip() or None
        loc.network_id = int(network_id) if network_id.strip() else None
        loc.latitude = float(latitude) if latitude.strip() else None
        loc.longitude = float(longitude) if longitude.strip() else None
        loc.cost_per_kwh = float(cost_per_kwh) if cost_per_kwh.strip() else None
        await db.commit()
    ctx = await _locations_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )


@router.post("/location/{location_id}/delete", response_class=HTMLResponse)
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
    ctx = await _locations_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )


@router.post("/network/{network_id}/delete", response_class=HTMLResponse)
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
    ctx = await _networks_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )


# ---------------------------------------------------------------------------
# Merge endpoints
# ---------------------------------------------------------------------------


@router.get("/network/{source_id}/merge-preview", response_class=HTMLResponse)
async def network_merge_preview(
    source_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Show merge preview modal for a network with counts of affected items."""
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source network not found")

    # Count affected rows
    session_count = (
        await db.execute(
            select(func.count()).select_from(EVChargingSession).where(
                EVChargingSession.network_id == source_id
            )
        )
    ).scalar() or 0

    subscription_count = (
        await db.execute(
            select(func.count()).select_from(EVNetworkSubscription).where(
                EVNetworkSubscription.network_id == source_id
            )
        )
    ).scalar() or 0

    location_count = (
        await db.execute(
            select(func.count()).select_from(EVLocationLookup).where(
                EVLocationLookup.network_id == source_id
            )
        )
    ).scalar() or 0

    # All networks except source for target dropdown
    all_nets = await get_all_networks(db)
    target_options = [n for n in all_nets if n.id != source_id]

    return templates.TemplateResponse(
        request,
        "review/partials/merge_network_modal.html",
        {
            "source": source,
            "session_count": session_count,
            "subscription_count": subscription_count,
            "location_count": location_count,
            "target_options": target_options,
        },
    )


@router.post("/network/{source_id}/merge", response_class=HTMLResponse)
async def merge_network(
    source_id: int,
    request: Request,
    target_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Merge source network into target: reassign all references, delete source."""
    if source_id == target_id:
        raise HTTPException(status_code=400, detail="Cannot merge a network into itself")

    # Validate both exist
    source = (
        await db.execute(select(EVChargingNetwork).where(EVChargingNetwork.id == source_id))
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source network not found")

    target = (
        await db.execute(select(EVChargingNetwork).where(EVChargingNetwork.id == target_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target network not found")

    # Reassign all FK references to target
    await db.execute(
        update(EVChargingSession)
        .where(EVChargingSession.network_id == source_id)
        .values(network_id=target_id)
    )
    await db.execute(
        update(EVNetworkSubscription)
        .where(EVNetworkSubscription.network_id == source_id)
        .values(network_id=target_id)
    )
    await db.execute(
        update(EVLocationLookup)
        .where(EVLocationLookup.network_id == source_id)
        .values(network_id=target_id)
    )

    # Create network name alias from source name for future auto-resolution
    existing_alias = await db.execute(
        select(EVNetworkNameAlias).where(
            func.lower(EVNetworkNameAlias.alias_name) == source.network_name.lower()
        )
    )
    if not existing_alias.scalar_one_or_none():
        db.add(EVNetworkNameAlias(
            network_id=target_id,
            alias_name=source.network_name,
        ))

    # Delete source
    await db.execute(delete(EVChargingNetwork).where(EVChargingNetwork.id == source_id))
    await db.commit()

    # Return refreshed networks table with HX-Trigger to close modal
    ctx = await _networks_context(db)
    response = templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )
    response.headers["HX-Trigger"] = "closeMergeModal"
    return response


@router.get("/location/{source_id}/merge-preview", response_class=HTMLResponse)
async def location_merge_preview(
    source_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Show merge preview modal for a location with counts of affected items."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source location not found")

    # Count affected rows
    session_count = (
        await db.execute(
            select(func.count()).select_from(EVChargingSession).where(
                EVChargingSession.location_id == source_id
            )
        )
    ).scalar() or 0

    stall_count = (
        await db.execute(
            select(func.count()).select_from(EVChargerStall).where(
                EVChargerStall.location_id == source_id
            )
        )
    ).scalar() or 0

    # All locations except source for target dropdown
    all_locs_result = await db.execute(
        select(EVLocationLookup).order_by(EVLocationLookup.location_name)
    )
    all_locs = list(all_locs_result.scalars().all())
    target_options = [loc for loc in all_locs if loc.id != source_id]

    return templates.TemplateResponse(
        request,
        "review/partials/merge_location_modal.html",
        {
            "source": source,
            "session_count": session_count,
            "stall_count": stall_count,
            "target_options": target_options,
        },
    )


@router.post("/location/{source_id}/merge", response_class=HTMLResponse)
async def merge_location(
    source_id: int,
    request: Request,
    target_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Merge source location into target: reassign all references, delete source."""
    if source_id == target_id:
        raise HTTPException(status_code=400, detail="Cannot merge a location into itself")

    # Validate both exist
    source = (
        await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == source_id))
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source location not found")

    target = (
        await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == target_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target location not found")

    # Reassign all FK references to target
    await db.execute(
        update(EVChargingSession)
        .where(EVChargingSession.location_id == source_id)
        .values(location_id=target_id)
    )
    await db.execute(
        update(EVChargerStall)
        .where(EVChargerStall.location_id == source_id)
        .values(location_id=target_id)
    )

    # Create GPS alias from source coordinates for future location memory
    if source.latitude is not None and source.longitude is not None:
        from web.queries.locations import haversine_meters, LOCATION_MATCH_RADIUS_M
        # Check if an existing alias for the same target is already within 100m
        existing_aliases_result = await db.execute(
            select(EVLocationGPSAlias).where(
                EVLocationGPSAlias.location_id == target_id
            )
        )
        existing_aliases = list(existing_aliases_result.scalars().all())
        already_covered = False
        for alias in existing_aliases:
            dist = haversine_meters(
                float(alias.latitude), float(alias.longitude),
                float(source.latitude), float(source.longitude),
            )
            if dist <= LOCATION_MATCH_RADIUS_M:
                already_covered = True
                break

        if not already_covered:
            db.add(EVLocationGPSAlias(
                location_id=target_id,
                latitude=float(source.latitude),
                longitude=float(source.longitude),
                source="merge",
            ))

    # Delete source
    await db.execute(delete(EVLocationLookup).where(EVLocationLookup.id == source_id))
    await db.commit()

    # Return refreshed locations table with HX-Trigger to close modal
    ctx = await _locations_context(db)
    response = templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )
    response.headers["HX-Trigger"] = "closeMergeModal"
    return response
