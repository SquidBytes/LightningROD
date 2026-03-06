"""CSV import routes for uploading and processing charging session CSV files."""

import csv
import io
import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.settings import get_app_setting
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
    headers = [f["field"] for f in DB_FIELD_OPTIONS]
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

    preview_rows = transformed[:25]
    import_data = _serialize_rows(transformed)

    return templates.TemplateResponse(
        request,
        "settings/partials/import_preview.html",
        {
            "preview_rows": preview_rows,
            "total_rows": total_rows,
            "new_count": new_count,
            "dup_count": dup_count,
            "error_count": error_count,
            "import_data_json": json.dumps(import_data),
            "matched_columns": matched_columns,
            "unmatched_columns": unmatched_columns,
            "import_timezone": import_timezone,
        },
    )


@router.post("/settings/import/preview", response_class=HTMLResponse)
async def preview_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Re-verify endpoint for inline editing (kept for future Plan 03 use).

    The standard upload flow now goes directly from upload to preview via
    upload_csv().  This route is retained for potential re-verification needs.
    """
    form = await request.form()

    # Extract raw CSV data from hidden fields
    csv_data_raw = form.get("csv_data", "")

    try:
        raw_rows: list[dict] = json.loads(csv_data_raw) if csv_data_raw else []
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid CSV data in form — please re-upload your file."},
        )

    # Transform rows (no column mapping needed — data is already transformed)
    # Detect duplicates against the database
    transformed = await detect_duplicates(raw_rows, db)

    total_rows = len(transformed)
    new_count = sum(1 for r in transformed if r.get("_status") == "new")
    dup_count = sum(1 for r in transformed if r.get("_status") in ("duplicate", "fuzzy_duplicate"))
    error_count = sum(1 for r in transformed if r.get("_status") == "error")

    preview_rows = transformed[:25]
    import_data = _serialize_rows(transformed)

    return templates.TemplateResponse(
        request,
        "settings/partials/import_preview.html",
        {
            "preview_rows": preview_rows,
            "total_rows": total_rows,
            "new_count": new_count,
            "dup_count": dup_count,
            "error_count": error_count,
            "import_data_json": json.dumps(import_data),
            "matched_columns": [],
            "unmatched_columns": [],
            "import_timezone": "UTC",
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

    # Re-parse row values from JSON serialized form (datetimes are ISO strings)
    # The rows are already in serialized form; import_rows handles string UUIDs/datetimes
    # via the EVChargingSession model accepting strings for UUID columns
    # We need to convert back to proper types for the model
    rows = _deserialize_rows(rows)

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
    return templates.TemplateResponse(
        request,
        "settings/partials/import_tab.html",
        {
            "db_fields": get_db_field_options(),
            "user_tz": user_tz,
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
