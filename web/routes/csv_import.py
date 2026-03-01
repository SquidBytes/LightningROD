"""CSV import routes for uploading and processing charging session CSV files."""

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web.services.csv_parser import (
    auto_detect_mappings,
    get_db_field_options,
    parse_csv_file,
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
    import json

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
