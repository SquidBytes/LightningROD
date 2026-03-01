import math
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.dependencies import get_db
from web.queries.costs import compute_session_cost, get_networks_by_name
from web.queries.sessions import query_sessions

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/sessions", response_class=HTMLResponse)
async def sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    date_preset: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    charge_type: Optional[str] = None,
    location_type: Optional[str] = None,
    hx_request: Annotated[Optional[str], Header()] = None,
):
    session_list, total, summary = await query_sessions(
        db=db,
        page=page,
        date_preset=date_preset,
        date_from=date_from,
        date_to=date_to,
        charge_type=charge_type,
        location_type=location_type,
    )

    total_pages = max(math.ceil(total / 25), 1)

    # Enrich sessions with cost data
    networks_by_name = await get_networks_by_name(db)
    enriched_sessions = []
    for s in session_list:
        cost_info = compute_session_cost(s, networks_by_name)
        enriched_sessions.append({"session": s, "cost_info": cost_info})

    # Build clean filter_params dict for pagination URLs (exclude page, exclude None)
    filter_params: dict = {}
    if date_preset:
        filter_params["date_preset"] = date_preset
    if date_from:
        filter_params["date_from"] = date_from
    if date_to:
        filter_params["date_to"] = date_to
    if charge_type:
        filter_params["charge_type"] = charge_type
    if location_type:
        filter_params["location_type"] = location_type

    context = {
        "sessions": enriched_sessions,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "summary": summary,
        "date_preset": date_preset,
        "date_from": date_from,
        "date_to": date_to,
        "charge_type": charge_type,
        "location_type": location_type,
        "filter_params": filter_params,
        "active_page": "sessions",
        "page_title": "Sessions",
    }

    if hx_request:
        return templates.TemplateResponse(request, "sessions/partials/table.html", context)
    return templates.TemplateResponse(request, "sessions/index.html", context)


@router.get("/sessions/{session_id}/detail", response_class=HTMLResponse)
async def session_detail(
    request: Request,
    session_id: int,
    prev_id: Optional[int] = None,
    next_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()

    if session is None:
        return HTMLResponse(content="<p class='text-gray-400 p-4'>Session not found.</p>", status_code=404)

    networks_by_name = await get_networks_by_name(db)
    cost_info = compute_session_cost(session, networks_by_name)

    context = {
        "session": session,
        "cost_info": cost_info,
        "prev_id": prev_id,
        "next_id": next_id,
    }
    return templates.TemplateResponse(request, "sessions/partials/drawer.html", context)
