"""Review queue routes for locations, networks, and duplicate sessions."""


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
    q: str | None = None,
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
    q: str | None = None,
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


async def _approved_tree_context(db: AsyncSession) -> dict:
    """Build the tree context for the Approved tab: approved networks with
    their approved locations nested, plus a list of standalone approved
    locations (network_id IS NULL)."""
    # Approved networks
    nets_result = await db.execute(
        select(EVChargingNetwork)
        .where(EVChargingNetwork.is_verified == True)  # noqa: E712
        .order_by(EVChargingNetwork.network_name)
    )
    approved_networks = list(nets_result.scalars().all())

    # Approved locations (all)
    locs_result = await db.execute(
        select(EVLocationLookup)
        .where(EVLocationLookup.is_verified == True)  # noqa: E712
        .order_by(EVLocationLookup.location_name)
    )
    approved_locations = list(locs_result.scalars().all())

    # Group by network_id
    locations_by_network: dict[int, list] = {}
    standalone_locations: list = []
    for loc in approved_locations:
        if loc.network_id is None:
            standalone_locations.append(loc)
        else:
            locations_by_network.setdefault(loc.network_id, []).append(loc)

    # Session counts per network
    net_session_rows = await db.execute(
        select(EVChargingSession.network_id, func.count())
        .where(EVChargingSession.network_id.is_not(None))
        .group_by(EVChargingSession.network_id)
    )
    net_session_counts = {nid: cnt for nid, cnt in net_session_rows.all()}

    # Session counts per location
    loc_session_rows = await db.execute(
        select(EVChargingSession.location_id, func.count())
        .where(EVChargingSession.location_id.is_not(None))
        .group_by(EVChargingSession.location_id)
    )
    loc_session_counts = {lid: cnt for lid, cnt in loc_session_rows.all()}

    return {
        "approved_networks": approved_networks,
        "locations_by_network": locations_by_network,
        "standalone_locations": standalone_locations,
        "net_session_counts": net_session_counts,
        "loc_session_counts": loc_session_counts,
    }


@router.get("", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    tab: str = "pending",
    sub: str = "networks",
    q: str | None = None,
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
):
    """Review queue page with Pending/Approved top-level tabs."""
    # Validate tabs
    if tab not in ("pending", "approved"):
        tab = "pending"
    if sub not in ("networks", "locations"):
        sub = "networks"

    # Build context for the active tab
    tab_ctx: dict = {}
    if tab == "pending":
        if sub == "networks":
            tab_ctx = await _networks_context(db, q=q, filter="unverified", sort=sort)
        else:
            tab_ctx = await _locations_context(db, q=q, filter="unverified", sort=sort)
    else:
        tab_ctx = await _approved_tree_context(db)

    # Pending counts for the top-level Pending tab badge
    pending_net_result = await db.execute(
        select(func.count())
        .select_from(EVChargingNetwork)
        .where(EVChargingNetwork.is_verified == False)  # noqa: E712
    )
    pending_networks = pending_net_result.scalar() or 0

    pending_loc_result = await db.execute(
        select(func.count())
        .select_from(EVLocationLookup)
        .where(EVLocationLookup.is_verified == False)  # noqa: E712
    )
    pending_locations = pending_loc_result.scalar() or 0

    approved_net_result = await db.execute(
        select(func.count())
        .select_from(EVChargingNetwork)
        .where(EVChargingNetwork.is_verified == True)  # noqa: E712
    )
    approved_networks_count = approved_net_result.scalar() or 0

    approved_loc_result = await db.execute(
        select(func.count())
        .select_from(EVLocationLookup)
        .where(EVLocationLookup.is_verified == True)  # noqa: E712
    )
    approved_locations_count = approved_loc_result.scalar() or 0

    pending_count = pending_networks + pending_locations
    approved_count = approved_networks_count + approved_locations_count

    active_vehicle = await get_active_vehicle(db)
    all_vehicles = await get_all_vehicles(db)

    return templates.TemplateResponse(
        request,
        "review/review_queue.html",
        {
            **tab_ctx,
            "active_tab": tab,
            "active_sub": sub,
            "pending_count": pending_count,
            "approved_count": approved_count,
            "pending_networks_count": pending_networks,
            "pending_locations_count": pending_locations,
            "active_page": "review_queue",
            "page_title": "Review Queue",
            "active_vehicle": active_vehicle,
            "all_vehicles": all_vehicles,
        },
    )


@router.get("/networks", response_class=HTMLResponse)
async def review_networks(
    request: Request,
    q: str | None = None,
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
):
    """Pending networks sub-tab partial -- forced unverified filter."""
    ctx = await _networks_context(db, q=q, filter="unverified", sort=sort)
    return templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )


@router.get("/locations", response_class=HTMLResponse)
async def review_locations(
    request: Request,
    q: str | None = None,
    sort: str = "name",
    db: AsyncSession = Depends(get_db),
):
    """Pending locations sub-tab partial -- forced unverified filter."""
    ctx = await _locations_context(db, q=q, filter="unverified", sort=sort)
    return templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )


@router.get("/approved", response_class=HTMLResponse)
async def review_approved(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Approved tab partial -- tree view of approved networks + locations."""
    ctx = await _approved_tree_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_approved_tree.html",
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
    ctx = await _locations_context(db, filter="unverified")
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
    ctx = await _networks_context(db, filter="unverified")
    return templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )


@router.post("/network/{network_id}/unverify", response_class=HTMLResponse)
async def unverify_network(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark a network as unverified and refresh the Approved tab tree.

    This flips ``is_verified`` to ``False`` and keeps the existing
    ``source_system`` value unchanged.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    net = result.scalar_one_or_none()
    if net:
        net.is_verified = False
        await db.commit()
    ctx = await _approved_tree_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_approved_tree.html",
        ctx,
    )


@router.post("/location/{location_id}/unverify", response_class=HTMLResponse)
async def unverify_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark a location as unverified and refresh the Approved tab tree.

    This flips ``is_verified`` to ``False`` and keeps the existing
    ``source_system`` value unchanged.
    """
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc:
        loc.is_verified = False
        await db.commit()
    ctx = await _approved_tree_context(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_approved_tree.html",
        ctx,
    )


@router.get("/networks/{network_id}/edit-modal", response_class=HTMLResponse)
async def review_edit_network_modal(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the shared network edit modal in review-page mode.

    The template is configured to post back to review routes and target
    the review content container.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    return templates.TemplateResponse(
        request,
        "settings/partials/network_edit_modal.html",
        {
            "network": network,
            "save_url": f"/review/networks/{network_id}",
            "save_target": "#review-inner",
            "save_swap": "innerHTML",
            "caller_context": "review",
        },
    )


@router.put("/networks/{network_id}", response_class=HTMLResponse)
async def review_edit_network(
    network_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_name: str = Form(...),
    cost_per_kwh: float | None = Form(None),
    color: str | None = Form(None),
    is_free: str | None = Form(None),
    notes: str | None = Form(None),
):
    """Save edits for one network from the review page.

    Returns the refreshed pending-networks table and sets
    ``HX-Trigger: closeNetworkModal`` so the client closes the modal.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    net = result.scalar_one_or_none()
    if net is not None:
        net.network_name = network_name
        net.cost_per_kwh = cost_per_kwh
        net.color = color.strip() if color and color.strip() else None
        net.is_free = is_free is not None
        if notes is not None:
            net.notes = notes.strip() or None
        await db.commit()
    ctx = await _networks_context(db, filter="unverified")
    response = templates.TemplateResponse(
        request,
        "review/partials/review_networks_table.html",
        ctx,
    )
    response.headers["HX-Trigger"] = "closeNetworkModal"
    return response


@router.get("/location/{location_id}/edit-form", response_class=HTMLResponse)
async def review_location_edit_form(
    location_id: int,
    request: Request,
    return_to: str = "pending",  # Caller tab context: pending or approved.
    db: AsyncSession = Depends(get_db),
):
    """Render the location edit form used by the shared page-level modal.

    ``return_to`` preserves the caller tab so the save endpoint can return
    the correct partial for either Pending or Approved views.
    """
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    all_networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_location_edit_form.html",
        {
            "loc": loc,
            "all_networks": all_networks,
            "return_to": return_to,
        },
    )


@router.get("/location/{location_id}/associate-modal", response_class=HTMLResponse)
async def associate_location_modal(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the Associate picker form for the page-scope #edit-loc-modal
    dialog.
    The picker is a lightweight alternative to the full location edit modal —
    just a network <datalist> combobox + submit. Associating from here does
    not change ``loc.is_verified``; it only sets ``network_id``.
    """
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    all_networks = await get_all_networks(db)
    return templates.TemplateResponse(
        request,
        "review/partials/review_associate_modal.html",
        {"loc": loc, "all_networks": all_networks},
    )


@router.post("/location/{location_id}/associate", response_class=HTMLResponse)
async def associate_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    network_id: str = Form(""),
    network_name: str = Form(""),
):
    """Associate location."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")

    nid: int | None = None
    if network_id.strip():
        nid = int(network_id)
    elif network_name.strip():
        # Case-insensitive lookup so the user typing "chargepoint" vs
        # "ChargePoint" resolves to the same row without creating a dup.
        existing = (
            await db.execute(
                select(EVChargingNetwork).where(
                    func.lower(EVChargingNetwork.network_name)
                    == network_name.strip().lower()
                )
            )
        ).scalar_one_or_none()
        if existing:
            nid = existing.id
        else:
            new_net = EVChargingNetwork(
                network_name=network_name.strip(),
                is_verified=False,
                source_system="manual",
            )
            db.add(new_net)
            await db.flush()
            nid = new_net.id

    if nid is not None:
        loc.network_id = nid
        # Association is intentionally separate from verification.
    await db.commit()

    ctx = await _locations_context(db, filter="unverified")
    response = templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
        ctx,
    )
    response.headers["HX-Trigger"] = "closeEditLocModal"
    return response


@router.post("/location/{location_id}/promote", response_class=HTMLResponse)
async def promote_location(
    location_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new network from this location and mark both as verified.

    This is the one-click path for places that should be treated as their
    own network.
    """
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")

    new_net = EVChargingNetwork(
        network_name=loc.location_name,
        is_verified=True,
        source_system="manual",
    )
    db.add(new_net)
    await db.flush()  # populate id before linking

    loc.network_id = new_net.id
    loc.is_verified = True
    loc.source_system = "manual"
    await db.commit()

    ctx = await _locations_context(db, filter="unverified")
    return templates.TemplateResponse(
        request,
        "review/partials/review_locations_table.html",
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
    return_to: str = Form("pending"),  # Caller tab context: pending or approved.
):
    """Save edits for one location and return the matching review partial.

    Numeric form fields are accepted as strings so blank values can be saved
    as ``None`` without a validation error. ``return_to`` chooses whether the
    response refreshes the Pending table or the Approved tree.
    """
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
    # Refresh the partial that matches the caller tab.
    if return_to == "approved":
        ctx = await _approved_tree_context(db)
        response = templates.TemplateResponse(
            request,
            "review/partials/review_approved_tree.html",
            ctx,
        )
    else:
        ctx = await _locations_context(db, filter="unverified")
        response = templates.TemplateResponse(
            request,
            "review/partials/review_locations_table.html",
            ctx,
        )
    response.headers["HX-Trigger"] = "closeEditLocModal"
    return response


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
    ctx = await _locations_context(db, filter="unverified")
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
    ctx = await _networks_context(db, filter="unverified")
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
    return_to: str = "pending",  # caller tab context (pending|approved)
    db: AsyncSession = Depends(get_db),
):
    """Show merge preview modal for a network with counts of affected items.

    `return_to` plumbs the caller's active tab through to the POST handler
    so the response partial lands in the correct swap zone. The value is
    echoed into a hidden input and compared as ``== "approved"``.
    """
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
            "return_to": return_to,
        },
    )


@router.post("/network/{source_id}/merge", response_class=HTMLResponse)
async def merge_network(
    source_id: int,
    request: Request,
    target_id: int = Form(...),
    return_to: str = Form("pending"),  # pending|approved
    db: AsyncSession = Depends(get_db),
):
    """Merge source network into target: reassign all references, delete source.

    `return_to` selects the response partial so the caller's active tab
    sees its own swap zone refresh. `approved` -> approved-tree partial
    into #review-content; anything else -> networks table into
    #review-inner.
    """
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

    # Render the correct partial for the caller's active tab.
    # Approved callers land back on the tree (#review-content); Pending
    # callers land back on the networks table (#review-inner). Both paths
    # emit HX-Trigger: closeMergeModal so the page-scope merge dialog closes.
    if return_to == "approved":
        ctx = await _approved_tree_context(db)
        response = templates.TemplateResponse(
            request,
            "review/partials/review_approved_tree.html",
            ctx,
        )
    else:
        ctx = await _networks_context(db, filter="unverified")
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
    return_to: str = "pending",  # pending|approved
    db: AsyncSession = Depends(get_db),
):
    """Show merge preview modal for a location with counts of affected items.

    `return_to` is echoed into a hidden input inside the rendered form so
    the subsequent POST carries the caller's tab context.
    """
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
            "return_to": return_to,
        },
    )


@router.post("/location/{source_id}/merge", response_class=HTMLResponse)
async def merge_location(
    source_id: int,
    request: Request,
    target_id: int = Form(...),
    return_to: str = Form("pending"),  # pending|approved
    db: AsyncSession = Depends(get_db),
):
    """Merge source location into target: reassign all references, delete source.

    `return_to` selects the response partial — `approved` returns the
    approved-tree into #review-content so an Approved-tab user sees their
    tree refresh; anything else returns the locations table into
    #review-inner.
    """
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
        from web.queries.locations import LOCATION_MATCH_RADIUS_M, haversine_meters
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

    # Render the correct partial for the caller's active tab.
    # Approved callers land back on the tree (#review-content); Pending
    # callers land back on the locations table (#review-inner). Both paths
    # emit HX-Trigger: closeMergeModal so the page-scope merge dialog closes.
    if return_to == "approved":
        ctx = await _approved_tree_context(db)
        response = templates.TemplateResponse(
            request,
            "review/partials/review_approved_tree.html",
            ctx,
        )
    else:
        ctx = await _locations_context(db, filter="unverified")
        response = templates.TemplateResponse(
            request,
            "review/partials/review_locations_table.html",
            ctx,
        )
    response.headers["HX-Trigger"] = "closeMergeModal"
    return response
