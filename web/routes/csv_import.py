"""CSV import routes for uploading and processing charging session CSV files."""

import json

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.services.csv_parser import (
    auto_detect_mappings,
    detect_duplicates,
    get_db_field_options,
    parse_csv_file,
    transform_rows,
)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.post("/settings/import/upload", response_class=HTMLResponse)
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
) -> HTMLResponse:
    """Accept a CSV file upload, parse headers, and return the column mapper partial.

    Reads file bytes, parses CSV, extracts headers, auto-detects column mappings,
    and renders the column mapper template with the results.

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
        headers, rows = parse_csv_file(contents)
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

    # Get DB field options and auto-detect mappings
    db_fields = get_db_field_options()
    auto_mappings = auto_detect_mappings(headers, db_fields)

    # Serialize rows to JSON string to pass through the template as a hidden field
    # (stateless approach — no server-side session storage)
    rows_json = json.dumps(rows)
    headers_json = json.dumps(headers)

    return templates.TemplateResponse(
        request,
        "settings/partials/column_mapper.html",
        {
            "csv_headers": headers,
            "db_fields": db_fields,
            "auto_mappings": auto_mappings,
            "row_count": len(rows),
            "rows_json": rows_json,
            "headers_json": headers_json,
            "filename": file.filename or "uploaded.csv",
        },
    )


@router.post("/settings/import/preview", response_class=HTMLResponse)
async def preview_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Accept column mapping form data and return the import preview table.

    Parses the mapping fields (mapping_{csv_header}), transforms all rows,
    runs duplicate detection, and renders the preview partial with first 25 rows.
    """
    form = await request.form()

    # Extract raw CSV data from hidden fields
    csv_data_raw = form.get("csv_data", "")
    csv_headers_raw = form.get("csv_headers", "")

    try:
        raw_rows: list[dict] = json.loads(csv_data_raw) if csv_data_raw else []
        csv_headers: list[str] = json.loads(csv_headers_raw) if csv_headers_raw else []
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid CSV data in form — please re-upload your file."},
        )

    # Extract column mapping from form fields (mapping_{csv_header} -> db_field)
    column_mapping: dict[str, str] = {}
    for key, value in form.items():
        if key.startswith("mapping_"):
            csv_header = key[len("mapping_"):]
            column_mapping[csv_header] = str(value)

    # Transform rows
    transformed = transform_rows(raw_rows, column_mapping)

    # Detect duplicates against the database
    transformed = await detect_duplicates(transformed, db)

    # Compute summary stats
    total_rows = len(transformed)
    new_count = sum(1 for r in transformed if r.get("_status") == "new")
    dup_count = sum(1 for r in transformed if r.get("_status") in ("duplicate", "fuzzy_duplicate"))
    error_count = sum(1 for r in transformed if r.get("_status") == "error")

    # Prepare preview rows (first 25) — make datetime objects JSON-serializable
    preview_rows = transformed[:25]

    # Serialize all transformed rows for hidden import_data field
    # Convert non-serializable types (datetime, uuid) to strings
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
        },
    )


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
