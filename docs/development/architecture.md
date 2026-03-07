# Architecture

How LightningROD is structured and the patterns used throughout the codebase.

## Overview

LightningROD is a server-rendered web application. The backend handles all data access, computation, and HTML rendering. The frontend uses HTMX for dynamic updates without full page reloads, DaisyUI for UI components, and Plotly for interactive charts.

```
Browser (HTMX + DaisyUI + Plotly)
    |
FastAPI (routes, templates)
    |
Query Layer (web/queries/)
    |
SQLAlchemy 2.0 async ORM
    |
PostgreSQL 16
```

## Project Structure

```
LightningROD/
├── config.py                # Application settings (reads .env)
├── docker-compose.yml       # Production stack (web + db)
├── docker-compose.dev.yml   # Dev override (exposes db port)
├── Dockerfile               # Multi-stage build (Node CSS + Python app)
├── entrypoint.sh            # Migrations + uvicorn startup
├── input.css                # Tailwind v4 + DaisyUI source styles
├── package.json             # Node deps (tailwindcss, daisyui)
│
├── db/
│   ├── engine.py            # Async SQLAlchemy engine + session factory
│   ├── models/              # ORM models (9 tables)
│   └── migrations/          # Alembic migration files
│
├── web/
│   ├── main.py              # FastAPI app factory
│   ├── dependencies.py      # Database session dependency
│   ├── routes/              # Route handlers
│   ├── queries/             # Data access layer
│   ├── services/            # Business logic (csv_parser, etc.)
│   ├── templates/           # Jinja2 templates with HTMX partials
│   └── static/              # Compiled CSS, vendor JS (HTMX, Plotly)
│
├── scripts/
│   └── seed.py              # CSV-to-PostgreSQL import
│
└── data/                    # CSV files for seeding (gitignored)
```

## Application Startup

The FastAPI app is created by the factory function in `web/main.py`. On startup:

1. The `lifespan` context manager initializes the database engine
2. Jinja2 templates are loaded from `web/templates/`
3. Static files are mounted from `web/static/`
4. Route modules are included from `web/routes/`

In Docker, `entrypoint.sh` runs Alembic migrations before starting uvicorn.

## Request Flow

### Full Page Request

```
Browser                   FastAPI                    Query Layer              PostgreSQL
   |                         |                           |                       |
   |-- GET /sessions ------->|                           |                       |
   |                         |-- query_sessions() ------>|                       |
   |                         |                           |-- SELECT ... -------->|
   |                         |                           |<-- rows --------------|
   |                         |<-- (data, total, summary) |                       |
   |                         |                           |                       |
   |                         |-- render sessions/index.html                      |
   |<-- full HTML page ------|                           |                       |
```

### HTMX Partial Update

When filtering or sorting, HTMX sends a request with `HX-Request: true`. The route returns only the partial template:

```
Browser                   FastAPI
   |                         |
   |-- GET /sessions ------->|  (with HX-Request header)
   |   ?charge_type=DC       |
   |                         |-- render sessions/partials/table.html
   |<-- table HTML only -----|
   |                         |
   (HTMX swaps into page)
```

## Key Patterns

### Query Layer Separation

Route handlers do not contain SQL or ORM queries. All data access goes through `web/queries/`:

```python
# Route handler -- HTTP concerns only
sessions, total, summary = await query_sessions(db, filters, page, per_page)
return templates.TemplateResponse("sessions/index.html", {
    "sessions": sessions,
    "total": total,
    "summary": summary,
})
```

```python
# Query function -- data access only
async def query_sessions(db, filters, page, per_page):
    query = select(EVChargingSession)
    # Apply filters...
    result = await db.execute(query)
    return sessions, total, summary
```

### HTMX Partial Rendering

Routes check for the `HX-Request` header to decide between full page and partial:

```python
if request.headers.get("HX-Request"):
    return templates.TemplateResponse("sessions/partials/table.html", context)
return templates.TemplateResponse("sessions/index.html", context)
```

### Settings as Key-Value Store

User preferences are stored in `app_settings` as key-value pairs:

```python
# Read multiple settings in one query
settings = await get_app_settings_dict(db, [
    "gas_price", "vehicle_mpg", "efficiency_unit", "user_timezone"
])

# Write with upsert semantics
await set_app_setting(db, "efficiency_unit", "eu")
```

### Cost Hierarchy

Session costs follow a cascade: location `cost_per_kwh` > network `cost_per_kwh` > no estimate. The `estimated_cost` field is stored on the session record.

## Template Structure

Each page has an `index.html` and a `partials/` subdirectory:

```
templates/
├── base.html                     # Master layout (sidebar + content area)
├── partials/
│   ├── modal_shell.html          # Shared modal component
│   ├── filter_bar.html           # Shared date-range filter bar
│   └── pagination.html           # Shared pagination component
├── sessions/
│   ├── index.html                # Full page
│   └── partials/
│       ├── table.html            # Session table (HTMX target)
│       ├── filters.html          # Session-specific filters
│       ├── drawer.html           # Session detail drawer
│       ├── modal.html            # Session edit modal (3 tabs)
│       └── add_form.html         # Add session form
├── settings/
│   ├── index.html                # Settings page (tabbed)
│   └── partials/
│       ├── network_management.html
│       ├── network_edit_modal.html
│       ├── location_rows.html
│       ├── stall_rows.html
│       ├── import_tab.html
│       ├── import_preview.html
│       ├── import_row.html
│       └── ...
├── dashboard/
│   └── index.html
├── costs/
│   ├── index.html
│   └── partials/
└── energy/
    ├── index.html
    └── partials/
```

The `base.html` template provides the dark-mode sidebar layout using DaisyUI's drawer component, loads HTMX and Plotly from vendored static files.

## UI Component Library

The app uses [DaisyUI v5](https://daisyui.com/) as a CSS-only component library on top of Tailwind CSS v4. Components used throughout:

- `btn`, `badge`, `card`, `table` -- Core layout
- `tabs`, `modal`, `drawer` -- Navigation and overlays
- `select`, `checkbox`, `input` -- Form controls
- `stats` -- Metric displays

DaisyUI is CSS-only (zero JavaScript), which means components work correctly after HTMX partial swaps without re-initialization.
