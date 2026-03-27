# LightningROD

Self-hosted charging analytics for Ford electric vehicles.

Track charging sessions, analyze costs, and monitor energy consumption with a web-based dashboard. Built for the Ford F-150 Lightning, designed to work with any Ford EV using data from [ha-fordpass](https://github.com/marq24/ha_fordpass) or CSV imports.

![overview](assets/images/lr_overview.gif)

---

## Why So Many Data Fields?

LightningROD stores a lot of fields on purpose. Charging analytics becomes much more useful when raw and derived data are both available.

- **Different calculations need different inputs** -- costs, EVSE loss/utilization, comparisons, and trend charts each rely on different fields.
- **Data quality varies by source** -- Home Assistant, CSV files, and manual entry all provide different levels of detail, so fields are optional and can be filled incrementally.
- **Recalculation needs history** -- when rates, subscriptions, networks, or mappings change, stored detail allows recalculating without losing fidelity.
- **Auditability matters** -- keeping source-oriented fields makes it easier to trace where numbers came from (manual, import, HASS, stall defaults, estimated).

In short: more fields means better calculations, safer backfills, and fewer assumptions.

---

## Features

**Charging Sessions** -- Full CRUD with sorting, date presets, charge-type and network filters, group edit, and a detail drawer with cost breakdown, EVSE metrics, stall context, and charge-curve preview.

**Cost Analytics** -- Cost hierarchy (manual/imported, location override, network, subscription member rate), actual vs estimated tracking, subscription savings, and gas/network comparison scenarios.

**Energy Dashboard** -- Total energy, regen recovery summary, efficiency trend with rolling average, and monthly energy by charge type.

**Home Dashboard** -- Multi-vehicle overview, global summary cards, EVSE charging-efficiency aggregates, monthly energy by network chart, and network energy breakdown.

**Network & Location Management** -- Color-coded networks, expandable location management, per-location cost overrides, charger stall definitions (with template pre-fill), and subscription period management.

**CSV Import** -- Template download plus auto-detection fallback, timezone-aware parsing, inline row correction for errors/duplicates, and support for full session/EVSE field mapping.

**Home Assistant Integration** -- Real-time connection to Home Assistant via WebSocket for automatic charging session detection from [ha-fordpass](https://github.com/marq24/ha_fordpass). Vehicle telemetry ingestion, VIN auto-detection, unit normalization, and 30-day history backfill.

**Settings** -- Vehicle profiles, comparison display options, gas price history/sensor integration, unit preferences (US/EU), timezone selection, and Home Assistant connection controls.

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
