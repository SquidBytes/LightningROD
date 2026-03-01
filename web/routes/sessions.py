import math
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.dependencies import get_db
from web.queries.costs import compute_session_cost, get_networks_by_name
from web.queries.sessions import get_most_recent_location, query_sessions

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

VALID_LOCATION_TYPES = {"home", "work", "public"}
VALID_CHARGE_TYPES = {"AC", "DC"}


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


@router.get("/sessions/new", response_class=HTMLResponse)
async def new_session_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render blank add-session form with smart defaults."""
    default_location = await get_most_recent_location(db)
    context = {
        "default_date": date.today().isoformat(),
        "default_location": default_location,
    }
    return templates.TemplateResponse(request, "sessions/partials/add_form.html", context)


@router.post("/sessions", response_class=HTMLResponse)
async def create_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_date: Annotated[Optional[str], Form()] = None,
    energy_kwh: Annotated[Optional[float], Form()] = None,
    cost: Annotated[Optional[float], Form()] = None,
    location_name: Annotated[Optional[str], Form()] = None,
    location_type: Annotated[Optional[str], Form()] = None,
    charge_type: Annotated[Optional[str], Form()] = None,
    duration_minutes: Annotated[Optional[float], Form()] = None,
):
    errors: dict[str, str] = {}

    # Validate required fields
    if not session_date:
        errors["session_date"] = "Date is required."
    else:
        try:
            parsed_date = datetime.fromisoformat(session_date).replace(tzinfo=timezone.utc)
        except ValueError:
            errors["session_date"] = "Invalid date format. Use YYYY-MM-DD."

    if energy_kwh is None:
        errors["energy_kwh"] = "Energy (kWh) is required."
    elif energy_kwh <= 0:
        errors["energy_kwh"] = "Energy must be greater than 0."

    # Validate optional enum fields
    if location_type and location_type not in VALID_LOCATION_TYPES:
        errors["location_type"] = f"Must be one of: {', '.join(sorted(VALID_LOCATION_TYPES))}"
    if charge_type and charge_type not in VALID_CHARGE_TYPES:
        errors["charge_type"] = f"Must be one of: {', '.join(VALID_CHARGE_TYPES)}"

    if errors:
        return JSONResponse(status_code=422, content={"errors": errors})

    # Determine is_free based on cost
    is_free: Optional[bool] = None
    if cost is not None:
        is_free = cost == 0

    new_session = EVChargingSession(
        session_id=uuid.uuid4(),
        device_id="manual",
        session_start_utc=parsed_date,
        energy_kwh=energy_kwh,
        cost=cost if cost is not None else None,
        cost_source="manual" if cost is not None else None,
        location_name=location_name or None,
        location_type=location_type or None,
        charge_type=charge_type or None,
        charge_duration_seconds=duration_minutes * 60 if duration_minutes is not None else None,
        is_complete=True,
        source_system="manual_entry",
        is_free=is_free,
    )

    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)

    networks_by_name = await get_networks_by_name(db)
    cost_info = compute_session_cost(new_session, networks_by_name)

    context = {
        "session": new_session,
        "cost_info": cost_info,
        "prev_id": None,
        "next_id": None,
    }
    response = templates.TemplateResponse(request, "sessions/partials/drawer.html", context)
    response.headers["HX-Trigger"] = "session-created"
    return response


@router.put("/sessions/{session_id}", response_class=HTMLResponse)
async def update_session(
    request: Request,
    session_id: int,
    db: AsyncSession = Depends(get_db),
    cost: Annotated[Optional[float], Form()] = None,
    location_name: Annotated[Optional[str], Form()] = None,
    location_type: Annotated[Optional[str], Form()] = None,
    charge_type: Annotated[Optional[str], Form()] = None,
    charge_duration_minutes: Annotated[Optional[float], Form()] = None,
):
    # Validate enum fields
    errors: dict[str, str] = {}
    if location_type and location_type not in VALID_LOCATION_TYPES:
        errors["location_type"] = f"Must be one of: {', '.join(sorted(VALID_LOCATION_TYPES))}"
    if charge_type and charge_type not in VALID_CHARGE_TYPES:
        errors["charge_type"] = f"Must be one of: {', '.join(VALID_CHARGE_TYPES)}"
    if errors:
        return JSONResponse(status_code=422, content={"errors": errors})

    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return HTMLResponse(content="<p class='text-gray-400 p-4'>Session not found.</p>", status_code=404)

    # Update editable fields — only apply fields that were submitted
    if cost is not None:
        session.cost = cost
        session.cost_source = "manual"
    if location_name is not None:
        session.location_name = location_name or None
    if location_type is not None:
        session.location_type = location_type or None
    if charge_type is not None:
        session.charge_type = charge_type or None
    if charge_duration_minutes is not None:
        session.charge_duration_seconds = charge_duration_minutes * 60

    await db.commit()
    await db.refresh(session)

    networks_by_name = await get_networks_by_name(db)
    cost_info = compute_session_cost(session, networks_by_name)

    context = {
        "session": session,
        "cost_info": cost_info,
        "prev_id": None,
        "next_id": None,
    }
    response = templates.TemplateResponse(request, "sessions/partials/drawer.html", context)
    response.headers["HX-Trigger"] = "session-updated"
    return response


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return HTMLResponse(content="Session not found.", status_code=404)

    await db.delete(session)
    await db.commit()

    return Response(
        content="",
        status_code=200,
        headers={
            "HX-Trigger": "session-deleted",
            "HX-Reswap": "none",
        },
    )


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
