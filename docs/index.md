# LightningROD

Self-hosted charging analytics for Ford electric vehicles.

Track charging sessions, analyze costs, and monitor energy consumption with a web-based dashboard. Built for the Ford F-150 Lightning, designed to work with any Ford EV using data from [ha-fordpass](https://github.com/marq24/ha_fordpass) or CSV imports.

![overview](assets/images/lr_overview.gif)

---

## Features

**Charging Sessions** -- Full session management with add, edit, delete, and CSV import. Paginated table with sort-by-column, multi-select network filtering, date range presets, and charge type/location type filters. Detail drawer with EVSE charger data and cost breakdown.

**Cost Analytics** -- Per-network and per-location cost rates with estimated cost calculation. Lifetime spending by network, gas vehicle savings comparison, and network cost comparison. Actual vs estimated cost tracking.

**Energy Dashboard** -- Total energy consumed, efficiency trends, charge type breakdown, and aggregate charging efficiency metrics (loss %, utilization %).

**Dashboard** -- Summary cards with total sessions, energy, cost, and miles. Monthly cost trend, energy by network, and efficiency trend charts. Charging efficiency card with average loss and utilization.

**Network Management** -- Charging networks with color badges, expandable location management, per-location cost overrides, and charger stall configuration with EVSE specs.

**CSV Import** -- Template-based import with auto-detection fallback, inline error/duplicate editing, timezone-aware parsing, and import summary.

**Home Assistant Integration** -- Real-time connection to Home Assistant via WebSocket for automatic charging session detection from [ha-fordpass](https://github.com/marq24/ha_fordpass). Vehicle telemetry ingestion, VIN auto-detection, unit normalization, and 30-day history backfill.

**Settings** -- Network management, gas comparison parameters, unit preferences (US/EU), timezone selection, Home Assistant connection, and comparison section toggles.

---

## Quick Start

```bash
git clone https://github.com/SquidBytes/LightningROD.git
cd LightningROD
cp .env.example .env
docker compose up --build -d
```

The app will be available at `http://localhost:8000`. See [Installation](getting-started/installation.md) for full details.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Web framework | FastAPI |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Templates | Jinja2 |
| Frontend | HTMX 2.0, Tailwind CSS v4, DaisyUI v5, Plotly |
| Deployment | Docker Compose |
| Docs | [Zensical](https://zensical.org/) |
