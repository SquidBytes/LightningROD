"""Locations routes — HTMX-friendly dropdown helpers for locations keyed by network.
Sessions group-edit bar cascades from a selected network to a filtered location dropdown. 
`GET /locations/by-network` returns an `<option>` fragment for direct innerHTML swap.
"""


from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVLocationLookup
from web.dependencies import get_db

router = APIRouter()


@router.get("/locations/by-network", response_class=HTMLResponse)
async def locations_by_network(
    request: Request,
    network_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Return an `<option>` fragment of verified locations for a given network.

    Used by the Sessions group-edit bar to populate `#bulk-location-id` when
    a network is chosen. Returns a placeholder option when `network_id` is
    missing so callers can safely swap the result directly into a `<select>`.
    Declared str: the combobox's cleared/partial-typed state sends
    `network_id=`, which must reset the options rather than 422 and leave
    stale ones in place.
    """
    try:
        network_id_int = int(network_id) if network_id else None
    except (TypeError, ValueError):
        network_id_int = None
    if not network_id_int:
        return HTMLResponse('<option value="">Location (select network first)</option>')

    stmt = (
        select(EVLocationLookup)
        .where(EVLocationLookup.network_id == network_id_int)
        .where(EVLocationLookup.is_verified == True)  # noqa: E712
        .order_by(EVLocationLookup.location_name)
    )
    result = await db.execute(stmt)
    locations = list(result.scalars().all())

    options = ['<option value="">— No change —</option>']
    for loc in locations:
        # Minimal HTML-escape of location_name for safety (names are user-controlled)
        name = (loc.location_name or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        options.append(f'<option value="{loc.id}">{name}</option>')
    return HTMLResponse("".join(options))
