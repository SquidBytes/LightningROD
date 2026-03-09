"""CSV import routes for uploading and processing charging session CSV files."""

import csv
import io
import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.settings import get_all_networks, get_app_setting, resolve_network
from web.queries.vehicles import get_active_vehicle, get_all_vehicles, get_vehicle_by_id
from web.services.csv_parser import (
    DB_FIELD_OPTIONS,
    auto_detect_mappings,
    detect_duplicates,
    get_db_field_options,
    import_rows,
    parse_csv_file,
    transform_rows,
)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/settings/import/template")
async def download_template() -> Response:
    """Generate and return an empty CSV template with all DB field headers.

    Returns a CSV file with a single header row (no data rows) containing every
    mappable EVChargingSession column name.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [f.get("csv_header", f["field"]) for f in DB_FIELD_OPTIONS]
    writer.writerow(headers)
    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="lightningrod_import_template.csv"',
        },
    )


@router.post("/settings/import/upload", response_class=HTMLResponse)
async def upload_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    import_timezone: str = Form("UTC"),
) -> HTMLResponse:
    """Accept a CSV file upload, auto-detect columns, and render preview directly.

    Skips the column mapper step entirely.  Reads file bytes, parses CSV,
    auto-detects column mappings silently, transforms rows with timezone-aware
    parsing, runs duplicate detection, and renders the import preview.

    Returns 422 JSONResponse if the file is empty or cannot be parsed.
    """
    # Read file contents
    contents = await file.read()

    if not contents:
        return JSONResponse(
            status_code=422,
            content={"detail": "Uploaded file is empty."},
        )

    # Parse CSV
    try:
        headers, raw_rows = parse_csv_file(contents)
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc)},
        )

    if not headers:
        return JSONResponse(
            status_code=422,
            content={"detail": "CSV file has no column headers."},
        )

    # Auto-detect column mappings silently
    db_fields = get_db_field_options()
    auto_mappings = auto_detect_mappings(headers, db_fields)

    # Compute matched / unmatched columns for informational banner
    field_label_map = {f["field"]: f["label"] for f in db_fields}
    matched_columns = [
        {"csv_header": h, "db_field_label": field_label_map.get(auto_mappings[h], auto_mappings[h])}
        for h in headers
        if h in auto_mappings
    ]
    unmatched_columns = [h for h in headers if h not in auto_mappings]

    # Transform rows with timezone-aware parsing
    transformed = transform_rows(raw_rows, auto_mappings, import_tz=import_timezone)

    # Detect duplicates against the database
    transformed = await detect_duplicates(transformed, db)

    # Compute summary stats
    total_rows = len(transformed)
    new_count = sum(1 for r in transformed if r.get("_status") == "new")
    dup_count = sum(1 for r in transformed if r.get("_status") in ("duplicate", "fuzzy_duplicate"))
    error_count = sum(1 for r in transformed if r.get("_status") == "error")

    import_data = _serialize_rows(transformed)
    all_networks = await get_all_networks(db)
    vehicles = await get_all_vehicles(db)
    active_vehicle = await get_active_vehicle(db)

    return templates.TemplateResponse(
        request,
        "settings/partials/import_preview.html",
        {
            "preview_rows": transformed,
            "total_rows": total_rows,
            "new_count": new_count,
            "dup_count": dup_count,
            "error_count": error_count,
            "import_data_json": json.dumps(import_data),
            "matched_columns": matched_columns,
            "unmatched_columns": unmatched_columns,
            "import_timezone": import_timezone,
            "networks": all_networks,
            "vehicles": vehicles,
            "active_vehicle": active_vehicle,
        },
    )


@router.post("/settings/import/verify-row", response_class=HTMLResponse)
async def verify_row(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Re-verify a single edited row and return updated row HTML.

    Accepts form data with editable field values, re-transforms and re-checks
    for duplicates, and returns a replacement <tbody> fragment containing the
    updated data row and inline editor.
    """
    form = await request.form()

    row_index = int(form.get("row_index", "0"))
    import_timezone = str(form.get("import_timezone", "UTC"))
    editor_open = bool(form.get("editor_open", ""))

    # Resolve network: combobox sends network_id (hidden) and network_name (visible)
    form_network_id = form.get("network_id", "")
    form_network_name = form.get("network_name", "")
    resolved_network_id = await resolve_network(
        db,
        network_id=int(form_network_id) if form_network_id else None,
        network_name=form_network_name if form_network_name else None,
    )

    # Build a raw row dict from submitted field values
    editable_fields = [
        "session_start_utc", "energy_kwh", "location_name", "cost",
        "charge_type", "charge_duration_seconds",
    ]
    raw_row: dict[str, str] = {}
    for field in editable_fields:
        val = form.get(field, "")
        if val:
            raw_row[field] = str(val)
    if resolved_network_id:
        raw_row["network_id"] = str(resolved_network_id)

    # Create identity mapping (values are already keyed by DB field name)
    column_mapping = {f: f for f in raw_row}

    # Transform and detect duplicates
    transformed = transform_rows([raw_row], column_mapping, import_tz=import_timezone)
    if transformed:
        transformed[0]["_row_index"] = row_index
        transformed = await detect_duplicates(transformed, db)

    row = transformed[0] if transformed else {
        "_row_index": row_index,
        "_status": "error",
        "_error": "No data provided",
    }

    all_networks = await get_all_networks(db)

    # Return HTML fragment: a <tbody> wrapping the data row and editor row
    return templates.TemplateResponse(
        request,
        "settings/partials/import_row.html",
        {
            "row": row,
            "row_index": row_index,
            "import_timezone": import_timezone,
            "editor_open": editor_open,
            "networks": all_networks,
        },
    )


@router.post("/settings/import/execute", response_class=HTMLResponse)
async def execute_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Accept the confirmed import form, commit selected rows, and return summary.

    Reads import_data (JSON string of all transformed rows), selected_rows (list of
    checked row indices), and action_{row_index} fields for duplicate row actions.
    Calls import_rows, then renders the summary partial.
    """
    form = await request.form()

    # Parse the full rows payload from the hidden import_data field
    import_data_raw = form.get("import_data", "")
    try:
        rows: list[dict] = json.loads(import_data_raw) if import_data_raw else []
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid import data — please restart the import."},
        )

    # Collect selected row indices from repeated checkbox values
    selected_rows_raw = form.getlist("selected_rows")
    selected_indices: set[int] = set()
    for val in selected_rows_raw:
        try:
            selected_indices.add(int(val))
        except (ValueError, TypeError):
            pass

    # Collect per-row duplicate actions: action_{row_index} -> "skip"|"insert"|"update"
    duplicate_actions: dict[int, str] = {}
    for key, value in form.items():
        if key.startswith("action_"):
            try:
                row_idx = int(key[len("action_"):])
                duplicate_actions[row_idx] = str(value)
            except (ValueError, TypeError):
                pass

    # Determine device_id for imported sessions from vehicle picker
    import_vehicle_id = form.get("import_vehicle_id")
    import_device_id = None
    if import_vehicle_id:
        try:
            vehicle = await get_vehicle_by_id(db, int(import_vehicle_id))
            if vehicle:
                import_device_id = vehicle.device_id
        except (ValueError, TypeError):
            pass
    if not import_device_id:
        active_vehicle = await get_active_vehicle(db)
        if active_vehicle:
            import_device_id = active_vehicle.device_id

    # Re-parse row values from JSON serialized form (datetimes are ISO strings)
    # The rows are already in serialized form; import_rows handles string UUIDs/datetimes
    # via the EVChargingSession model accepting strings for UUID columns
    # We need to convert back to proper types for the model
    rows = _deserialize_rows(rows)

    # Assign selected vehicle's device_id to all imported rows
    if import_device_id:
        for row in rows:
            row["device_id"] = import_device_id

    # Execute the import
    counts = await import_rows(rows, selected_indices, duplicate_actions, db)

    return templates.TemplateResponse(
        request,
        "settings/partials/import_summary.html",
        {
            "added": counts["added"],
            "skipped": counts["skipped"],
            "updated": counts["updated"],
            "failed": counts["failed"],
        },
    )


@router.get("/settings/import/reset", response_class=HTMLResponse)
async def reset_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Return the initial import file picker so the user can start a new import.

    Called by the 'Import Another File' button on the summary page via HTMX.
    """
    user_tz = await get_app_setting(db, "user_timezone", "UTC") or "UTC"
    vehicles = await get_all_vehicles(db)
    active_vehicle = await get_active_vehicle(db)
    return templates.TemplateResponse(
        request,
        "settings/partials/import_tab.html",
        {
            "db_fields": get_db_field_options(),
            "user_tz": user_tz,
            "vehicles": vehicles,
            "active_vehicle": active_vehicle,
        },
    )


def _deserialize_rows(rows: list[dict]) -> list[dict]:
    """Convert JSON-serialized row dicts back to typed values for DB insertion.

    Converts ISO datetime strings back to aware datetime objects and UUID strings
    back to uuid.UUID instances.
    """
    from datetime import datetime
    import uuid as _uuid

    result = []
    for row in rows:
        deserialized = {}
        for k, v in row.items():
            if isinstance(v, str) and k in (
                "session_start_utc",
                "session_end_utc",
                "recorded_at",
                "estimated_end_utc",
                "original_timestamp",
            ):
                try:
                    dt = datetime.fromisoformat(v)
                    deserialized[k] = dt
                except (ValueError, TypeError):
                    deserialized[k] = None
            elif isinstance(v, str) and k == "session_id":
                try:
                    deserialized[k] = _uuid.UUID(v)
                except (ValueError, AttributeError):
                    deserialized[k] = None
            else:
                deserialized[k] = v
        result.append(deserialized)
    return result


def _serialize_rows(rows: list[dict]) -> list[dict]:
    """Convert transformed rows to JSON-serializable dicts.

    Converts datetime objects to ISO strings and UUIDs to strings.
    """
    from datetime import datetime
    import uuid as _uuid

    result = []
    for row in rows:
        serialized = {}
        for k, v in row.items():
            if isinstance(v, datetime):
                serialized[k] = v.isoformat()
            elif isinstance(v, _uuid.UUID):
                serialized[k] = str(v)
            else:
                serialized[k] = v
        result.append(serialized)
    return result
