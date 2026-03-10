import json
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
from db.models.reference import EVChargerStall, EVLocationLookup
from web.dependencies import get_db
from web.queries.costs import compute_session_cost, get_locations_by_id, get_session_cost_context
from web.queries.sessions import get_most_recent_location, query_sessions
from web.queries.settings import get_all_networks, get_app_setting, get_stalls_for_location, get_subscriptions_for_network, resolve_network
from web.queries.vehicles import get_active_device_id, get_active_vehicle, get_all_vehicles

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

VALID_LOCATION_TYPES = {"home", "work", "public", "retail", "destination", "highway", "other"}
VALID_CHARGE_TYPES = {"AC", "DC"}


VALID_PER_PAGE = {25, 50, 100}


@router.get("/sessions", response_class=HTMLResponse)
async def sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    per_page: int = 25,
    date_preset: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    charge_type: Optional[str] = None,
    location_type: Optional[str] = None,
    network_id: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    hx_request: Annotated[Optional[str], Header()] = None,
):
    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    # Clamp per_page to allowed values
    if per_page not in VALID_PER_PAGE:
        per_page = 25

    # Parse comma-separated network_id values (e.g. "1,3,5") into a list of ints
    network_ids: Optional[list[int]] = None
    if network_id:
        try:
            network_ids = [int(v.strip()) for v in network_id.split(",") if v.strip()]
        except ValueError:
            network_ids = None

    session_list, total, summary = await query_sessions(
        db=db,
        page=page,
        per_page=per_page,
        date_preset=date_preset,
        date_from=date_from,
        date_to=date_to,
        charge_type=charge_type,
        location_type=location_type,
        network_ids=network_ids,
        sort_by=sort_by,
        sort_dir=sort_dir,
        device_id=active_device_id,
    )

    total_pages = max(math.ceil(total / per_page), 1)

    # Enrich sessions with cost data and build network map
    all_networks = await get_all_networks(db)
    network_map = {n.id: n for n in all_networks}
    user_tz = await get_app_setting(db, "user_timezone", "UTC")

    # Batch pre-load locations for sessions that have location_id
    location_ids = [s.location_id for s in session_list if s.location_id]
    locations_by_id = await get_locations_by_id(db, location_ids) if location_ids else {}

    enriched_sessions = []
    for s in session_list:
        network = network_map.get(s.network_id) if s.network_id else None
        location = locations_by_id.get(s.location_id) if s.location_id else None
        cost_info = compute_session_cost(s, network=network, location=location)
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
    if network_id:
        filter_params["network_id"] = network_id  # already a string (comma-separated)
    if sort_by:
        filter_params["sort_by"] = sort_by
    if sort_dir:
        filter_params["sort_dir"] = sort_dir
    if per_page != 25:
        filter_params["per_page"] = per_page

    all_vehicles = await get_all_vehicles(db)

    context = {
        "sessions": enriched_sessions,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "summary": summary,
        "date_preset": date_preset,
        "date_from": date_from,
        "date_to": date_to,
        "charge_type": charge_type,
        "location_type": location_type,
        "network_id": network_id,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "filter_params": filter_params,
        "network_map": network_map,
        "networks": all_networks,
        "user_tz": user_tz,
        "active_page": "sessions",
        "page_title": "Sessions",
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
    }

    if hx_request:
        return templates.TemplateResponse(request, "sessions/partials/table.html", context)
    return templates.TemplateResponse(request, "sessions/index.html", context)


@router.put("/sessions/bulk", response_class=HTMLResponse)
async def bulk_update_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Bulk update selected sessions with common field values."""
    form = await request.form()

    # Parse session IDs from comma-separated hidden input
    session_ids_str = form.get("session_ids", "")
    if not session_ids_str:
        return JSONResponse(status_code=422, content={"error": "No sessions selected"})

    try:
        session_ids = [int(sid.strip()) for sid in session_ids_str.split(",") if sid.strip()]
    except ValueError:
        return JSONResponse(status_code=422, content={"error": "Invalid session IDs"})

    if not session_ids:
        return JSONResponse(status_code=422, content={"error": "No sessions selected"})

    # Load sessions
    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.id.in_(session_ids))
    )
    bulk_sessions = result.scalars().all()

    # Apply updates only for fields that were submitted (non-empty)
    bulk_network_id = form.get("bulk_network_id")
    bulk_network_name = form.get("bulk_network_name")
    bulk_charge_type = form.get("bulk_charge_type")
    bulk_location_name = form.get("bulk_location_name")
    bulk_cost_str = form.get("bulk_cost")

    # Resolve network name to ID if name provided without ID
    if bulk_network_name and not bulk_network_id:
        resolved_id = await resolve_network(db, network_name=bulk_network_name)
        if resolved_id:
            bulk_network_id = str(resolved_id)

    updated = 0
    for s in bulk_sessions:
        changed = False
        if bulk_network_id is not None and bulk_network_id != "":
            s.network_id = int(bulk_network_id) if bulk_network_id != "clear" else None
            changed = True
        if bulk_charge_type is not None and bulk_charge_type != "":
            s.charge_type = bulk_charge_type if bulk_charge_type != "clear" else None
            changed = True
        if bulk_location_name is not None and bulk_location_name != "":
            s.location_name = bulk_location_name if bulk_location_name != "clear" else None
            changed = True
        if bulk_cost_str is not None and bulk_cost_str != "":
            s.cost = float(bulk_cost_str)
            s.cost_source = "manual"
            changed = True
        if changed:
            updated += 1

    await db.commit()

    # Return response that triggers table reload
    return Response(
        content="",
        status_code=200,
        headers={
            "HX-Trigger": json.dumps({"session-updated": {"bulk": True, "count": updated}}),
        },
    )


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


@router.get("/sessions/new/modal", response_class=HTMLResponse)
async def new_session_modal(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render advanced edit modal in add mode with smart defaults."""
    default_location = await get_most_recent_location(db)
    all_networks = await get_all_networks(db)
    context = {
        "session": None,
        "cost_info": None,
        "modal_mode": "add",
        "default_date": date.today().isoformat(),
        "default_location": default_location,
        "networks": all_networks,
        "stalls": [],
    }
    return templates.TemplateResponse(request, "sessions/partials/modal.html", context)


@router.post("/sessions", response_class=HTMLResponse)
async def create_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_date: Annotated[Optional[str], Form()] = None,
    session_time: Annotated[Optional[str], Form()] = None,
    energy_kwh: Annotated[Optional[float], Form()] = None,
    cost: Annotated[Optional[float], Form()] = None,
    location_name: Annotated[Optional[str], Form()] = None,
    location_type: Annotated[Optional[str], Form()] = None,
    charge_type: Annotated[Optional[str], Form()] = None,
    duration_minutes: Annotated[Optional[float], Form()] = None,
    charge_duration_minutes: Annotated[Optional[float], Form()] = None,
    max_power: Annotated[Optional[float], Form()] = None,
    min_power: Annotated[Optional[float], Form()] = None,
    charging_kw: Annotated[Optional[float], Form()] = None,
    charging_voltage: Annotated[Optional[float], Form()] = None,
    charging_amperage: Annotated[Optional[float], Form()] = None,
    start_soc: Annotated[Optional[float], Form()] = None,
    end_soc: Annotated[Optional[float], Form()] = None,
    miles_added: Annotated[Optional[float], Form()] = None,
    end_date: Annotated[Optional[str], Form()] = None,
    end_time: Annotated[Optional[str], Form()] = None,
    plugged_in_duration_minutes: Annotated[Optional[float], Form()] = None,
    location_id: Annotated[Optional[int], Form()] = None,
    plug_status: Annotated[Optional[str], Form()] = None,
    charging_status: Annotated[Optional[str], Form()] = None,
    network_id: Annotated[Optional[int], Form()] = None,
    network_name: Annotated[Optional[str], Form()] = None,
    is_free_form: Annotated[Optional[str], Form(alias="is_free")] = None,
    evse_voltage: Annotated[Optional[float], Form()] = None,
    evse_amperage: Annotated[Optional[float], Form()] = None,
    evse_kw: Annotated[Optional[float], Form()] = None,
    evse_energy_kwh: Annotated[Optional[float], Form()] = None,
    evse_max_power_kw: Annotated[Optional[float], Form()] = None,
    charger_rated_kw: Annotated[Optional[float], Form()] = None,
    stall_id: Annotated[Optional[int], Form()] = None,
    evse_source: Annotated[Optional[str], Form()] = None,
):
    errors: dict[str, str] = {}

    # Validate required fields
    if not session_date:
        errors["session_date"] = "Date is required."
    else:
        try:
            time_part = session_time or "00:00"
            parsed_date = datetime.fromisoformat(f"{session_date}T{time_part}").replace(tzinfo=timezone.utc)
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

    # Resolve network: prefer network_id, fall back to network_name lookup/auto-create
    network_id = await resolve_network(db, network_id=network_id, network_name=network_name)

    # Determine is_free: checkbox form value takes precedence; fall back to cost == 0
    is_free: Optional[bool] = None
    if is_free_form is not None:
        is_free = is_free_form in ('1', 'on', 'true')
    elif cost is not None:
        is_free = cost == 0

    # Support both old form name (duration_minutes) and new modal name (charge_duration_minutes)
    effective_duration = duration_minutes if duration_minutes is not None else charge_duration_minutes

    # Parse session_end_utc if end_date provided
    session_end_utc = None
    if end_date:
        end_time_part = end_time or "00:00"
        try:
            session_end_utc = datetime.fromisoformat(f"{end_date}T{end_time_part}").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Use active vehicle's device_id instead of hardcoded "manual"
    active_vehicle = await get_active_vehicle(db)
    session_device_id = active_vehicle.device_id if active_vehicle else "manual"

    new_session = EVChargingSession(
        session_id=uuid.uuid4(),
        device_id=session_device_id,
        session_start_utc=parsed_date,
        session_end_utc=session_end_utc,
        energy_kwh=energy_kwh,
        cost=cost if cost is not None else None,
        cost_source="manual" if cost is not None else None,
        location_name=location_name or None,
        location_type=location_type or None,
        location_id=location_id or None,
        network_id=network_id or None,
        charge_type=charge_type or None,
        charge_duration_seconds=effective_duration * 60 if effective_duration is not None else None,
        plugged_in_duration_seconds=plugged_in_duration_minutes * 60 if plugged_in_duration_minutes is not None else None,
        max_power=max_power or None,
        min_power=min_power or None,
        charging_kw=charging_kw or None,
        charging_voltage=charging_voltage or None,
        charging_amperage=charging_amperage or None,
        start_soc=start_soc,
        end_soc=end_soc,
        miles_added=miles_added or None,
        plug_status=plug_status or None,
        charging_status=charging_status or None,
        is_complete=True,
        source_system="manual_entry",
        is_free=is_free,
        evse_voltage=evse_voltage or None,
        evse_amperage=evse_amperage or None,
        evse_kw=evse_kw or None,
        evse_energy_kwh=evse_energy_kwh or None,
        evse_max_power_kw=evse_max_power_kw or None,
        charger_rated_kw=charger_rated_kw or None,
        stall_id=stall_id or None,
        evse_source=evse_source or None,
    )

    # Resolve location_id if not explicitly set and location data is available
    if not new_session.location_id:
        lat = new_session.latitude
        lon = new_session.longitude
        addr = new_session.address
        if (lat is not None and lon is not None) or addr:
            from web.queries.locations import resolve_location

            resolved_loc_id = await resolve_location(
                db,
                latitude=float(lat) if lat is not None else None,
                longitude=float(lon) if lon is not None else None,
                address=addr,
                network_id=new_session.network_id,
                location_name=new_session.location_name,
                source_system="manual",
            )
            if resolved_loc_id:
                new_session.location_id = resolved_loc_id

    # DC V/A estimation: if evse_kw set and V/A blank for DC sessions
    if new_session.charge_type == 'DC' and new_session.evse_kw and not new_session.evse_voltage and not new_session.evse_amperage:
        pack_voltage = 400  # F-150 Lightning ~400V pack
        new_session.evse_voltage = pack_voltage
        new_session.evse_amperage = float(new_session.evse_kw) * 1000 / pack_voltage
        if not new_session.evse_source:
            new_session.evse_source = 'estimated'

    # Set evse_source to stall_default when stall fills defaults and no explicit source
    if new_session.stall_id and not new_session.evse_source:
        new_session.evse_source = 'stall_default'

    db.add(new_session)

    # Compute estimated cost from hierarchy before commit
    all_networks = await get_all_networks(db)
    network_obj = next((n for n in all_networks if n.id == new_session.network_id), None) if new_session.network_id else None
    location_obj = None
    if new_session.location_id:
        loc_result = await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == new_session.location_id))
        location_obj = loc_result.scalar_one_or_none()

    est_rate = None
    if location_obj and location_obj.cost_per_kwh:
        est_rate = float(location_obj.cost_per_kwh)
    elif network_obj and network_obj.cost_per_kwh:
        est_rate = float(network_obj.cost_per_kwh)

    if est_rate and new_session.energy_kwh:
        new_session.estimated_cost = est_rate * float(new_session.energy_kwh)
    else:
        new_session.estimated_cost = None

    await db.commit()
    await db.refresh(new_session)

    cost_info = compute_session_cost(new_session, network=network_obj, location=location_obj)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")

    vehicles = await get_all_vehicles(db)
    context = {
        "session": new_session,
        "cost_info": cost_info,
        "prev_id": None,
        "next_id": None,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "user_tz": user_tz,
        "vehicles": vehicles,
    }
    response = templates.TemplateResponse(request, "sessions/partials/drawer.html", context)
    response.headers["HX-Trigger"] = json.dumps({
        "session-created": {"sessionId": new_session.id},
        "closeModal": None,
    })
    return response


@router.put("/sessions/{session_id}", response_class=HTMLResponse)
async def update_session(
    request: Request,
    session_id: int,
    db: AsyncSession = Depends(get_db),
    location_name: Annotated[Optional[str], Form()] = None,
    location_type: Annotated[Optional[str], Form()] = None,
    charge_type: Annotated[Optional[str], Form()] = None,
    charge_duration_minutes: Annotated[Optional[float], Form()] = None,
    energy_kwh: Annotated[Optional[float], Form()] = None,
    session_date: Annotated[Optional[str], Form()] = None,
    session_time: Annotated[Optional[str], Form()] = None,
    max_power: Annotated[Optional[float], Form()] = None,
    min_power: Annotated[Optional[float], Form()] = None,
    charging_kw: Annotated[Optional[float], Form()] = None,
    charging_voltage: Annotated[Optional[float], Form()] = None,
    charging_amperage: Annotated[Optional[float], Form()] = None,
    start_soc: Annotated[Optional[float], Form()] = None,
    end_soc: Annotated[Optional[float], Form()] = None,
    miles_added: Annotated[Optional[float], Form()] = None,
    end_date: Annotated[Optional[str], Form()] = None,
    end_time: Annotated[Optional[str], Form()] = None,
    plugged_in_duration_minutes: Annotated[Optional[float], Form()] = None,
    location_id: Annotated[Optional[int], Form()] = None,
    plug_status: Annotated[Optional[str], Form()] = None,
    charging_status: Annotated[Optional[str], Form()] = None,
    network_id: Annotated[Optional[int], Form()] = None,
    network_name: Annotated[Optional[str], Form()] = None,
    is_free: Annotated[Optional[str], Form()] = None,
    evse_voltage: Annotated[Optional[float], Form()] = None,
    evse_amperage: Annotated[Optional[float], Form()] = None,
    evse_kw: Annotated[Optional[float], Form()] = None,
    evse_energy_kwh: Annotated[Optional[float], Form()] = None,
    evse_max_power_kw: Annotated[Optional[float], Form()] = None,
    charger_rated_kw: Annotated[Optional[float], Form()] = None,
    stall_id: Annotated[Optional[int], Form()] = None,
    evse_source: Annotated[Optional[str], Form()] = None,
    vehicle_device_id: Annotated[Optional[str], Form()] = None,
):
    # Validate enum fields
    errors: dict[str, str] = {}
    if location_type and location_type not in VALID_LOCATION_TYPES:
        errors["location_type"] = f"Must be one of: {', '.join(sorted(VALID_LOCATION_TYPES))}"
    if charge_type and charge_type not in VALID_CHARGE_TYPES:
        errors["charge_type"] = f"Must be one of: {', '.join(VALID_CHARGE_TYPES)}"
    if session_date:
        try:
            datetime.fromisoformat(session_date)
        except ValueError:
            errors["session_date"] = "Invalid date format. Use YYYY-MM-DD."
    if errors:
        return JSONResponse(status_code=422, content={"errors": errors})

    # Resolve network: prefer network_id, fall back to network_name lookup/auto-create
    if network_name and not network_id:
        network_id = await resolve_network(db, network_name=network_name)

    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return HTMLResponse(content="<p class='text-gray-400 p-4'>Session not found.</p>", status_code=404)

    # Capture old network_id before any updates (for cost recalculation check)
    old_network_id = session.network_id

    # Update editable fields — only apply fields that were submitted
    # Only update cost if user explicitly changed it (not just re-submitted prefilled value)
    form_data = await request.form()
    submitted_cost = form_data.get("cost")
    if submitted_cost is not None and submitted_cost != "":
        new_cost = float(submitted_cost)
        if session.cost is None or abs(new_cost - float(session.cost)) > 0.001:
            session.cost = new_cost
            session.cost_source = "manual"
    if location_name is not None:
        session.location_name = location_name or None
    if location_type is not None:
        session.location_type = location_type or None
    if charge_type is not None:
        session.charge_type = charge_type or None
    if charge_duration_minutes is not None:
        session.charge_duration_seconds = charge_duration_minutes * 60
    if energy_kwh is not None:
        session.energy_kwh = energy_kwh
    if session_date:
        time_part = session_time or "00:00"
        try:
            new_start = datetime.fromisoformat(f"{session_date}T{time_part}").replace(tzinfo=timezone.utc)
            session.session_start_utc = new_start
        except ValueError:
            pass  # Keep existing value on parse error
    if end_date:
        end_time_part = end_time or "00:00"
        try:
            new_end = datetime.fromisoformat(f"{end_date}T{end_time_part}").replace(tzinfo=timezone.utc)
            session.session_end_utc = new_end
        except ValueError:
            pass
    if max_power is not None:
        session.max_power = max_power or None
    if min_power is not None:
        session.min_power = min_power or None
    if charging_kw is not None:
        session.charging_kw = charging_kw or None
    if charging_voltage is not None:
        session.charging_voltage = charging_voltage or None
    if charging_amperage is not None:
        session.charging_amperage = charging_amperage or None
    if start_soc is not None:
        session.start_soc = start_soc
    if end_soc is not None:
        session.end_soc = end_soc
    if miles_added is not None:
        session.miles_added = miles_added or None
    if plugged_in_duration_minutes is not None:
        session.plugged_in_duration_seconds = plugged_in_duration_minutes * 60
    if location_id is not None:
        session.location_id = location_id or None
    if plug_status is not None:
        session.plug_status = plug_status or None
    if charging_status is not None:
        session.charging_status = charging_status or None
    if network_id is not None:
        session.network_id = network_id or None
    if is_free is not None:
        session.is_free = is_free in ('1', 'on', 'true')

    # Update EVSE fields when submitted
    if evse_voltage is not None:
        session.evse_voltage = evse_voltage or None
    if evse_amperage is not None:
        session.evse_amperage = evse_amperage or None
    if evse_kw is not None:
        session.evse_kw = evse_kw or None
    if evse_energy_kwh is not None:
        session.evse_energy_kwh = evse_energy_kwh or None
    if evse_max_power_kw is not None:
        session.evse_max_power_kw = evse_max_power_kw or None
    if charger_rated_kw is not None:
        session.charger_rated_kw = charger_rated_kw or None
    if stall_id is not None:
        session.stall_id = stall_id or None
    if evse_source is not None:
        session.evse_source = evse_source or None

    # Vehicle reassignment via dropdown
    if vehicle_device_id is not None and vehicle_device_id != "":
        session.device_id = vehicle_device_id

    # Resolve location_id if not explicitly set by form and location data is available
    if location_id is None and session.location_id is None:
        s_lat = session.latitude
        s_lon = session.longitude
        s_addr = session.address
        if (s_lat is not None and s_lon is not None) or s_addr:
            from web.queries.locations import resolve_location as _resolve_loc

            resolved_loc = await _resolve_loc(
                db,
                latitude=float(s_lat) if s_lat is not None else None,
                longitude=float(s_lon) if s_lon is not None else None,
                address=s_addr,
                network_id=session.network_id,
                location_name=session.location_name,
                source_system="manual",
            )
            if resolved_loc:
                session.location_id = resolved_loc

    # DC V/A estimation: if evse_kw set and V/A blank for DC sessions
    if session.charge_type == 'DC' and session.evse_kw and not session.evse_voltage and not session.evse_amperage:
        pack_voltage = 400  # F-150 Lightning ~400V pack
        session.evse_voltage = pack_voltage
        session.evse_amperage = float(session.evse_kw) * 1000 / pack_voltage
        if not session.evse_source:
            session.evse_source = 'estimated'

    # Set evse_source to stall_default when stall fills defaults and no explicit source
    if session.stall_id and not session.evse_source:
        session.evse_source = 'stall_default'

    # Recalculate cost when network changes and cost was not manually set
    all_networks = await get_all_networks(db)
    if session.network_id != old_network_id and session.cost_source != 'manual':
        new_network = next((n for n in all_networks if n.id == session.network_id), None)
        if new_network and new_network.cost_per_kwh and session.energy_kwh:
            session.cost = float(new_network.cost_per_kwh) * float(session.energy_kwh)
            session.cost_source = 'calculated'

    # Compute estimated cost from hierarchy
    network_obj = next((n for n in all_networks if n.id == session.network_id), None) if session.network_id else None
    location_obj = None
    if session.location_id:
        loc_result = await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == session.location_id))
        location_obj = loc_result.scalar_one_or_none()

    est_rate = None
    if location_obj and location_obj.cost_per_kwh:
        est_rate = float(location_obj.cost_per_kwh)
    elif network_obj and network_obj.cost_per_kwh:
        est_rate = float(network_obj.cost_per_kwh)

    if est_rate and session.energy_kwh:
        session.estimated_cost = est_rate * float(session.energy_kwh)
    else:
        session.estimated_cost = None

    await db.commit()
    await db.refresh(session)

    cost_info = compute_session_cost(session, network=network_obj, location=location_obj)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    vehicles = await get_all_vehicles(db)

    context = {
        "session": session,
        "cost_info": cost_info,
        "prev_id": None,
        "next_id": None,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "user_tz": user_tz,
        "vehicles": vehicles,
    }
    response = templates.TemplateResponse(request, "sessions/partials/drawer.html", context)
    response.headers["HX-Trigger"] = json.dumps({
        "session-updated": {"sessionId": session.id},
        "closeModal": None,
    })
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

    network_obj, location_obj = await get_session_cost_context(db, session)
    sub_periods = await get_subscriptions_for_network(db, network_obj.id) if network_obj else []
    cost_info = compute_session_cost(session, network=network_obj, location=location_obj, subscription_periods=sub_periods)

    all_networks = await get_all_networks(db)
    vehicles = await get_all_vehicles(db)

    # Look up stall label if session has a stall_id
    stall_label = None
    if session.stall_id:
        stall_result = await db.execute(
            select(EVChargerStall).where(EVChargerStall.id == session.stall_id)
        )
        stall = stall_result.scalar_one_or_none()
        stall_label = stall.stall_label if stall else None

    user_tz = await get_app_setting(db, "user_timezone", "UTC")

    context = {
        "session": session,
        "cost_info": cost_info,
        "prev_id": prev_id,
        "next_id": next_id,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "stall_label": stall_label,
        "user_tz": user_tz,
        "vehicles": vehicles,
    }
    return templates.TemplateResponse(request, "sessions/partials/drawer.html", context)


@router.get("/sessions/{session_id}/modal", response_class=HTMLResponse)
async def session_modal(
    request: Request,
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Render advanced edit modal in edit mode for an existing session."""
    result = await db.execute(
        select(EVChargingSession).where(EVChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()

    if session is None:
        return HTMLResponse(content="<p class='text-gray-400 p-4'>Session not found.</p>", status_code=404)

    network_obj, location_obj = await get_session_cost_context(db, session)
    sub_periods = await get_subscriptions_for_network(db, network_obj.id) if network_obj else []
    cost_info = compute_session_cost(session, network=network_obj, location=location_obj, subscription_periods=sub_periods)

    all_networks = await get_all_networks(db)

    # Load stalls for session's location
    stalls = []
    if session.location_id:
        stalls = await get_stalls_for_location(db, session.location_id)

    user_tz = await get_app_setting(db, "user_timezone", "UTC")

    context = {
        "session": session,
        "cost_info": cost_info,
        "modal_mode": "edit",
        "default_date": None,
        "default_location": None,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "stalls": stalls,
        "user_tz": user_tz,
    }
    return templates.TemplateResponse(request, "sessions/partials/modal.html", context)
