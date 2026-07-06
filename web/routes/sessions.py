"""Charging-session CRUD, filtering, import, and drawer routes."""

import json
import math
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargerStall, EVLocationLookup
from web.dependencies import get_db
from web.queries.battery import build_mini_charge_curve, load_reference_charge_curve
from web.queries.costs import (
    compute_session_cost,
    get_locations_by_id,
    get_session_cost_context,
)
from web.queries.sessions import get_most_recent_location, query_sessions
from web.queries.settings import (
    get_all_networks,
    get_app_setting,
    get_stalls_for_location,
    get_subscriptions_for_network,
    get_unit_context,
    resolve_network,
)
from web.queries.vehicles import (
    get_active_device_id,
    get_active_vehicle,
    get_all_vehicles,
)
from web.unit_system import parse_user_local_to_utc, to_metric_distance

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

VALID_LOCATION_TYPES = {"home", "work", "public", "retail", "destination", "highway", "other"}
VALID_CHARGE_TYPES = {"AC", "DC"}


VALID_PER_PAGE = {25, 50, 100}


async def get_review_count(db: AsyncSession, device_id: str | None = None) -> int:
    """Count sessions needing review (duplicates + first-time auto-associations)."""
    stmt = select(func.count()).select_from(EVChargingSession).where(
        EVChargingSession.needs_review == True  # noqa: E712
    )
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)
    result = await db.execute(stmt)
    return result.scalar() or 0


async def _verified_locations_for_network(
    db: AsyncSession, network_id: int | None
) -> list[EVLocationLookup]:
    """initial server-rendered options for the session-edit
    location <select>. Mirrors GET /locations/by-network's filter (verified
    locations for the given network, ordered by name). Returns an empty list
    when network_id is falsy — the template renders the 'select network first'
    placeholder in that case.
    """
    if not network_id:
        return []
    stmt = (
        select(EVLocationLookup)
        .where(EVLocationLookup.network_id == network_id)
        .where(EVLocationLookup.is_verified == True)  # noqa: E712
        .order_by(EVLocationLookup.location_name)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/sessions", response_class=HTMLResponse)
async def sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    per_page: int = 25,
    date_preset: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    charge_type: str | None = None,
    location_type: str | None = None,
    network_id: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    hx_request: Annotated[str | None, Header()] = None,
):
    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    # Clamp per_page to allowed values
    if per_page not in VALID_PER_PAGE:
        per_page = 25

    # Parse comma-separated network_id values (e.g. "1,3,5") into a list of ints
    network_ids: list[int] | None = None
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
    review_count = await get_review_count(db, active_device_id)
    unit_ctx = await get_unit_context(db)

    context = {
        **unit_ctx,
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
        "review_count": review_count,
    }

    if hx_request:
        return templates.TemplateResponse(request, "sessions/partials/table.html", context)
    return templates.TemplateResponse(request, "sessions/index.html", context)


@router.get("/sessions/export.csv")
async def export_sessions_csv(
    db: AsyncSession = Depends(get_db),
    date_preset: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    charge_type: str | None = None,
    location_type: str | None = None,
    network_id: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
):
    """Download the charging-session list as CSV, honoring the active filters.

    Distances export in the configured display unit; times in the user's
    timezone; everything else in its stored canonical unit (kWh, kW, V, A, %).
    """
    import csv
    import io
    from zoneinfo import ZoneInfo

    from web.unit_system import convert_distance

    active_device_id = await get_active_device_id(db)
    network_ids: list[int] | None = None
    if network_id:
        try:
            network_ids = [int(v.strip()) for v in network_id.split(",") if v.strip()]
        except ValueError:
            network_ids = None

    session_list, _total, _summary = await query_sessions(
        db=db,
        page=1,
        per_page=1_000_000,
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

    network_names = {n.id: n.network_name for n in await get_all_networks(db)}
    unit_ctx = await get_unit_context(db)
    distance_unit = unit_ctx["distance_unit"]
    distance_label = unit_ctx["units"]["distance_label"]
    user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
    try:
        tz = ZoneInfo(user_tz)
    except Exception:
        tz = ZoneInfo("UTC")

    def _local(dt) -> str:
        if dt is None:
            return ""
        if dt.tzinfo is None:
            from datetime import UTC as _utc
            dt = dt.replace(tzinfo=_utc)
        return dt.astimezone(tz).isoformat(sep=" ", timespec="minutes")

    def _num(v, dp: int) -> str:
        return "" if v is None else f"{float(v):.{dp}f}"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        f"start ({user_tz})", f"end ({user_tz})", "charge_type", "network",
        "location_name", "location_type", "address", "energy_kwh",
        "evse_energy_kwh", "cost", "cost_source", "estimated_cost", "is_free",
        "start_soc_pct", "end_soc_pct", "charge_duration_min",
        "plugged_in_duration_min", "max_power_kw", "min_power_kw",
        "avg_power_kw", "charging_voltage", "charging_amperage",
        f"distance_added_{distance_label}",
    ])
    for s in session_list:
        writer.writerow([
            _local(s.session_start_utc),
            _local(s.session_end_utc),
            s.charge_type or "",
            network_names.get(s.network_id, "") if s.network_id else "",
            s.location_name or "",
            s.location_type or "",
            s.address or "",
            _num(s.energy_kwh, 2),
            _num(s.evse_energy_kwh, 2),
            _num(s.cost, 2),
            s.cost_source or "",
            _num(s.estimated_cost, 2),
            "" if s.is_free is None else str(bool(s.is_free)).lower(),
            _num(s.start_soc, 0),
            _num(s.end_soc, 0),
            _num(float(s.charge_duration_seconds) / 60 if s.charge_duration_seconds is not None else None, 0),
            _num(float(s.plugged_in_duration_seconds) / 60 if s.plugged_in_duration_seconds is not None else None, 0),
            _num(s.max_power, 1),
            _num(s.min_power, 1),
            _num(s.charging_kw, 1),
            _num(s.charging_voltage, 0),
            _num(s.charging_amperage, 0),
            _num(convert_distance(s.distance_added, distance_unit) if s.distance_added is not None else None, 1),
        ])

    filename = f"charging-sessions-{date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/sessions/bulk", response_class=HTMLResponse)
async def bulk_update_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Bulk update selected sessions with common field values."""
    form = await request.form()

    # Parse session IDs from comma-separated hidden input
    session_ids_str = str(form.get("session_ids", ""))
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
    bulk_network_id = str(form.get("bulk_network_id")) if form.get("bulk_network_id") is not None else None
    bulk_network_name = str(form.get("bulk_network_name")) if form.get("bulk_network_name") is not None else None
    bulk_charge_type = str(form.get("bulk_charge_type")) if form.get("bulk_charge_type") is not None else None
    bulk_location_id = str(form.get("bulk_location_id")) if form.get("bulk_location_id") is not None else None
    bulk_location_name = str(form.get("bulk_location_name")) if form.get("bulk_location_name") is not None else None
    bulk_cost_str = str(form.get("bulk_cost")) if form.get("bulk_cost") is not None else None

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
        if bulk_location_id is not None and bulk_location_id != "":
            s.location_id = int(bulk_location_id) if bulk_location_id != "clear" else None
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
    unit_ctx = await get_unit_context(db)
    context = {
        **unit_ctx,
        "session": None,
        "cost_info": None,
        "modal_mode": "add",
        "default_date": date.today().isoformat(),
        "default_location": default_location,
        "networks": all_networks,
        "network_locations": [],
        "stalls": [],
    }
    return templates.TemplateResponse(request, "sessions/partials/modal.html", context)


@router.post("/sessions", response_class=HTMLResponse)
async def create_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_date: Annotated[str | None, Form()] = None,
    session_time: Annotated[str | None, Form()] = None,
    energy_kwh: Annotated[float | None, Form()] = None,
    cost: Annotated[float | None, Form()] = None,
    location_name: Annotated[str | None, Form()] = None,
    location_type: Annotated[str | None, Form()] = None,
    charge_type: Annotated[str | None, Form()] = None,
    duration_minutes: Annotated[float | None, Form()] = None,
    charge_duration_minutes: Annotated[float | None, Form()] = None,
    max_power: Annotated[float | None, Form()] = None,
    min_power: Annotated[float | None, Form()] = None,
    charging_kw: Annotated[float | None, Form()] = None,
    charging_voltage: Annotated[float | None, Form()] = None,
    charging_amperage: Annotated[float | None, Form()] = None,
    start_soc: Annotated[float | None, Form()] = None,
    end_soc: Annotated[float | None, Form()] = None,
    distance_added: Annotated[float | None, Form()] = None,
    end_date: Annotated[str | None, Form()] = None,
    end_time: Annotated[str | None, Form()] = None,
    plugged_in_duration_minutes: Annotated[float | None, Form()] = None,
    location_id: Annotated[int | None, Form()] = None,
    plug_status: Annotated[str | None, Form()] = None,
    charging_status: Annotated[str | None, Form()] = None,
    network_id: Annotated[int | None, Form()] = None,
    network_name: Annotated[str | None, Form()] = None,
    is_free_form: Annotated[str | None, Form(alias="is_free")] = None,
    evse_voltage: Annotated[float | None, Form()] = None,
    evse_amperage: Annotated[float | None, Form()] = None,
    evse_kw: Annotated[float | None, Form()] = None,
    evse_energy_kwh: Annotated[float | None, Form()] = None,
    evse_max_power_kw: Annotated[float | None, Form()] = None,
    charger_rated_kw: Annotated[float | None, Form()] = None,
    stall_id: Annotated[int | None, Form()] = None,
    evse_source: Annotated[str | None, Form()] = None,
):
    errors: dict[str, str] = {}

    # Convert user-entered distance to metric (km) for storage
    unit_ctx = await get_unit_context(db)
    distance_added_km = to_metric_distance(distance_added, unit_ctx["distance_unit"]) if distance_added else None

    # User's configured timezone — submitted date/time form values are
    # interpreted in this zone before being converted to UTC for storage.
    create_user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"

    # Validate required fields
    if not session_date:
        errors["session_date"] = "Date is required."
    else:
        try:
            parsed_date = parse_user_local_to_utc(session_date, session_time, create_user_tz)
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
    is_free: bool | None = None
    if is_free_form is not None:
        is_free = is_free_form in ('1', 'on', 'true')
    elif cost is not None:
        is_free = cost == 0

    # Support both old form name (duration_minutes) and new modal name (charge_duration_minutes)
    effective_duration = duration_minutes if duration_minutes is not None else charge_duration_minutes

    # Parse session_end_utc if end_date provided — same TZ handling as start.
    session_end_utc = None
    if end_date:
        try:
            session_end_utc = parse_user_local_to_utc(end_date, end_time, create_user_tz)
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
        distance_added=distance_added_km,
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

    # Inherit network from the resolved location if none was supplied.
    from web.queries.locations import inherit_network_from_location
    new_session.network_id = await inherit_network_from_location(
        db, new_session.network_id, new_session.location_id
    )

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

    sub_periods = await get_subscriptions_for_network(db, network_obj.id) if network_obj else []
    cost_info = compute_session_cost(new_session, network=network_obj, location=location_obj, subscription_periods=sub_periods)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")

    vehicles = await get_all_vehicles(db)
    ref_data = load_reference_charge_curve(active_vehicle)
    ref_curve = ref_data["curve"] if ref_data else None
    mini_chart_html = build_mini_charge_curve(new_session, ref_curve=ref_curve)
    network_locations = await _verified_locations_for_network(db, new_session.network_id)
    context = {
        **unit_ctx,
        "session": new_session,
        "cost_info": cost_info,
        "prev_id": None,
        "next_id": None,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "network_locations": network_locations,
        "user_tz": user_tz,
        "vehicles": vehicles,
        "mini_chart_html": mini_chart_html,
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
    location_name: Annotated[str | None, Form()] = None,
    location_type: Annotated[str | None, Form()] = None,
    charge_type: Annotated[str | None, Form()] = None,
    charge_duration_minutes: Annotated[float | None, Form()] = None,
    energy_kwh: Annotated[float | None, Form()] = None,
    session_date: Annotated[str | None, Form()] = None,
    session_time: Annotated[str | None, Form()] = None,
    max_power: Annotated[float | None, Form()] = None,
    min_power: Annotated[float | None, Form()] = None,
    charging_kw: Annotated[float | None, Form()] = None,
    charging_voltage: Annotated[float | None, Form()] = None,
    charging_amperage: Annotated[float | None, Form()] = None,
    start_soc: Annotated[float | None, Form()] = None,
    end_soc: Annotated[float | None, Form()] = None,
    distance_added: Annotated[float | None, Form()] = None,
    end_date: Annotated[str | None, Form()] = None,
    end_time: Annotated[str | None, Form()] = None,
    plugged_in_duration_minutes: Annotated[float | None, Form()] = None,
    location_id: Annotated[int | None, Form()] = None,
    plug_status: Annotated[str | None, Form()] = None,
    charging_status: Annotated[str | None, Form()] = None,
    network_id: Annotated[int | None, Form()] = None,
    network_name: Annotated[str | None, Form()] = None,
    is_free: Annotated[str | None, Form()] = None,
    evse_voltage: Annotated[float | None, Form()] = None,
    evse_amperage: Annotated[float | None, Form()] = None,
    evse_kw: Annotated[float | None, Form()] = None,
    evse_energy_kwh: Annotated[float | None, Form()] = None,
    evse_max_power_kw: Annotated[float | None, Form()] = None,
    charger_rated_kw: Annotated[float | None, Form()] = None,
    stall_id: Annotated[int | None, Form()] = None,
    evse_source: Annotated[str | None, Form()] = None,
    vehicle_device_id: Annotated[str | None, Form()] = None,
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
        new_cost = float(str(submitted_cost))
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
    # Parse start/end timestamps in the user's configured timezone — the modal
    # pre-fills these inputs via the |localtime filter, so the submitted value
    # is local-time, not UTC.
    edit_user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
    if session_date:
        try:
            session.session_start_utc = parse_user_local_to_utc(
                session_date, session_time, edit_user_tz
            )
        except ValueError:
            pass  # Keep existing value on parse error
    if end_date:
        try:
            session.session_end_utc = parse_user_local_to_utc(
                end_date, end_time, edit_user_tz
            )
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
    if distance_added is not None:
        unit_ctx = await get_unit_context(db)
        session.distance_added = (
            to_metric_distance(distance_added, unit_ctx["distance_unit"]) or None
        )
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

    # Inherit network from the resolved location when the session still has none.
    from web.queries.locations import inherit_network_from_location
    session.network_id = await inherit_network_from_location(
        db, session.network_id, session.location_id
    )

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

    sub_periods = await get_subscriptions_for_network(db, network_obj.id) if network_obj else []
    cost_info = compute_session_cost(session, network=network_obj, location=location_obj, subscription_periods=sub_periods)
    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    vehicles = await get_all_vehicles(db)
    _vehicle = await get_active_vehicle(db)
    _ref_data = load_reference_charge_curve(_vehicle)
    _ref_curve = _ref_data["curve"] if _ref_data else None
    mini_chart_html = build_mini_charge_curve(session, ref_curve=_ref_curve)

    unit_ctx = await get_unit_context(db)
    network_locations = await _verified_locations_for_network(db, session.network_id)
    context = {
        **unit_ctx,
        "session": session,
        "cost_info": cost_info,
        "prev_id": None,
        "next_id": None,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "network_locations": network_locations,
        "user_tz": user_tz,
        "vehicles": vehicles,
        "mini_chart_html": mini_chart_html,
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
    prev_id: int | None = None,
    next_id: int | None = None,
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

    _vehicle = await get_active_vehicle(db)
    _ref_data = load_reference_charge_curve(_vehicle)
    _ref_curve = _ref_data["curve"] if _ref_data else None
    mini_chart_html = build_mini_charge_curve(session, ref_curve=_ref_curve)

    unit_ctx = await get_unit_context(db)
    network_locations = await _verified_locations_for_network(db, session.network_id)
    context = {
        **unit_ctx,
        "session": session,
        "cost_info": cost_info,
        "prev_id": prev_id,
        "next_id": next_id,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "network_locations": network_locations,
        "stall_label": stall_label,
        "user_tz": user_tz,
        "vehicles": vehicles,
        "mini_chart_html": mini_chart_html,
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

    # Cascade: server-render the location <select> options for the session's
    # current network so the dropdown is populated on first modal open. HTMX takes
    # over on subsequent network changes via GET /locations/by-network.
    network_locations = await _verified_locations_for_network(db, session.network_id)

    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    unit_ctx = await get_unit_context(db)

    context = {
        **unit_ctx,
        "session": session,
        "cost_info": cost_info,
        "modal_mode": "edit",
        "default_date": None,
        "default_location": None,
        "network_map": {n.id: n for n in all_networks},
        "networks": all_networks,
        "network_locations": network_locations,
        "stalls": stalls,
        "user_tz": user_tz,
    }
    return templates.TemplateResponse(request, "sessions/partials/modal.html", context)


@router.post("/sessions/check-duplicate", response_class=HTMLResponse)
async def check_duplicate(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_date: Annotated[str | None, Form()] = None,
    energy_kwh: Annotated[float | None, Form()] = None,
):
    """Check for potential duplicates before manual session creation.

    Returns a warning banner HTML if potential duplicates found, empty otherwise.
    """
    from datetime import timedelta

    from dateutil.parser import parse as parse_date

    if not session_date:
        return HTMLResponse("")

    try:
        start_dt = parse_date(session_date)
    except (ValueError, TypeError):
        return HTMLResponse("")

    window_start = start_dt - timedelta(hours=2)
    window_end = start_dt + timedelta(hours=2)

    stmt = (
        select(EVChargingSession)
        .where(EVChargingSession.session_start_utc.between(window_start, window_end))
    )

    result = await db.execute(stmt)
    matches = result.scalars().all()

    # Filter by energy tolerance if provided
    filtered = []
    for m in matches:
        if energy_kwh is not None and m.energy_kwh is not None:
            tolerance = abs(float(m.energy_kwh)) * 0.1 if float(m.energy_kwh) != 0 else 0.5
            if abs(float(energy_kwh) - float(m.energy_kwh)) <= tolerance:
                filtered.append(m)
        elif energy_kwh is None:
            filtered.append(m)  # No energy to compare, show all time matches

    if not filtered:
        return HTMLResponse("")

    # Return warning banner
    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    return templates.TemplateResponse(
        request,
        "sessions/partials/duplicate_warning.html",
        {"matches": filtered, "user_tz": user_tz},
    )


@router.get("/sessions/review-count-badge", response_class=HTMLResponse)
async def review_count_badge(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return badge HTML for session review count (for sidebar lazy load)."""
    count = await get_review_count(db)
    if count > 0:
        return HTMLResponse(f'<span class="badge badge-warning badge-sm">{count}</span>')
    return HTMLResponse("")


@router.get("/sessions/{session_id}/review-panel", response_class=HTMLResponse)
async def session_review_panel(
    session_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return inline comparison panel for a session needing review."""
    session = (
        await db.execute(
            select(EVChargingSession).where(EVChargingSession.id == session_id)
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    other = None
    if session.duplicate_of_id:
        other = (
            await db.execute(
                select(EVChargingSession).where(EVChargingSession.id == session.duplicate_of_id)
            )
        ).scalar_one_or_none()

    user_tz = await get_app_setting(db, "user_timezone", "UTC")
    return templates.TemplateResponse(
        request,
        "sessions/partials/review_panel.html",
        {"session": session, "other": other, "user_tz": user_tz},
    )


@router.post("/sessions/{session_id}/resolve-duplicate", response_class=HTMLResponse)
async def resolve_duplicate(
    session_id: int,
    request: Request,
    action: Annotated[str, Form()],  # 'keep_this', 'keep_other', 'keep_both'
    db: AsyncSession = Depends(get_db),
):
    """Resolve a duplicate session: keep one, delete the other, or keep both."""
    session = (
        await db.execute(
            select(EVChargingSession).where(EVChargingSession.id == session_id)
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if action == "keep_this":
        # Delete the other session, clear flags on this one
        if session.duplicate_of_id:
            await db.execute(
                delete(EVChargingSession).where(EVChargingSession.id == session.duplicate_of_id)
            )
        session.needs_review = False
        session.review_type = None
        session.duplicate_of_id = None

    elif action == "keep_other":
        # Delete this session
        other_id = session.duplicate_of_id
        await db.execute(
            delete(EVChargingSession).where(EVChargingSession.id == session_id)
        )
        # Clear review flags on the other session if it was also flagged
        if other_id:
            other = (await db.execute(
                select(EVChargingSession).where(EVChargingSession.id == other_id)
            )).scalar_one_or_none()
            if other and other.duplicate_of_id == session_id:
                other.needs_review = False
                other.review_type = None
                other.duplicate_of_id = None

    elif action == "keep_both":
        # Keep both, clear review flags
        session.needs_review = False
        session.review_type = None
        session.duplicate_of_id = None

    await db.commit()

    # Trigger table refresh
    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "sessionReviewResolved"
    return response
