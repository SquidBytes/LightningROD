# :lucide-layers: Architecture

How LightningROD is structured and the patterns used throughout the codebase.

## Overview

LightningROD is a server-rendered web application. The backend handles all data access, computation, and HTML rendering. The frontend uses HTMX for dynamic updates without full page reloads, and Plotly for interactive charts.

```
Browser (HTMX + Plotly)
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
├── Dockerfile               # Container image
├── entrypoint.sh            # Migrations + uvicorn startup
│
├── db/
│   ├── engine.py            # Async SQLAlchemy engine + session factory
│   ├── models/              # ORM models (8 tables)
│   └── migrations/          # Alembic migration files
│
├── web/
│   ├── main.py              # FastAPI app factory
│   ├── dependencies.py      # Database session dependency
│   ├── routes/              # Route handlers
│   ├── queries/             # Data access layer
│   └── templates/           # Jinja2 templates with HTMX partials
│
├── scripts/
│   └── seed.py              # CSV-to-PostgreSQL import
│
├── static/css/              # Compiled Tailwind CSS
└── data/                    # CSV files for seeding (gitignored)
```

## Application Startup

The FastAPI app is created by the factory function in `web/main.py`. On startup:

1. The `lifespan` context manager initializes the database engine
2. Jinja2 templates are loaded from `web/templates/`
3. Static files are mounted from `static/`
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

When filtering, HTMX sends a request with `HX-Request: true`. The route handler detects this and returns only the partial template:

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

```python title="web/routes/sessions.py"
# Route handler -- HTTP concerns only
sessions, total, summary = await query_sessions(db, filters, page, per_page)
return templates.TemplateResponse("sessions/index.html", {
    "sessions": sessions,
    "total": total,
    "summary": summary,
})
```

```python title="web/queries/sessions.py"
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

User preferences are stored in `app_settings` as key-value pairs. This avoids schema changes when adding new settings:

```python
# Read multiple settings in one query
settings = await get_app_settings_dict(db, [
    "gas_price", "vehicle_mpg", "efficiency_unit"
])

# Write with upsert semantics
await set_app_setting(db, "efficiency_unit", "eu")
```

### Cost Calculation

Session costs are computed at query time, not stored. `compute_session_cost()` takes a session and a networks dictionary, returns cost info based on the session's location and configured network cost. Changing a network cost immediately affects all displayed costs.

## Template Structure

Each page has an `index.html` and a `partials/` subdirectory:

```
templates/
├── base.html                     # Master layout (sidebar + content area)
├── sessions/
│   ├── index.html                # Full page
│   └── partials/
│       ├── table.html            # Session table (HTMX target)
│       ├── filters.html          # Filter bar
│       └── drawer.html           # Session detail drawer
├── costs/
│   ├── index.html
│   └── partials/
│       ├── summary_cards.html
│       ├── chart.html
│       └── comparisons.html
└── ...
```

The `base.html` template provides the dark-mode sidebar layout, loads HTMX from CDN, and includes Plotly for charts.
