# Changelog

All notable changes to LightningROD are documented here.

## v0.1.5 -- 2026-02-28

Initial release. Core charging analytics platform.

### Added

- **Infrastructure** -- Docker Compose stack with PostgreSQL 16, FastAPI, Alembic auto-migrations, and a dark-mode web UI (Jinja2 + HTMX + Tailwind).
- **Data Seed** -- CSV-to-PostgreSQL import script with idempotent upsert, automatic AC/DC classification, and location type assignment. 203 historical sessions loaded.
- **Charging Sessions** -- Paginated session list with filters for date range (presets and custom), charge type, and location. Slide-out detail drawer with all 30 session fields and prev/next navigation.
- **Cost Analytics** -- Configurable per-location electricity rates. Lifetime cost summary with free vs. paid breakdown. Gas vehicle comparison and network rate comparison with toggleable sections.
- **Energy Dashboard** -- Total lifetime energy consumed, efficiency trend chart with rolling average, regenerative braking totals (when data is available), and configurable US/EU unit display.
- **Settings** -- Rate management, gas comparison parameters (MPG, $/gallon), unit preferences, and comparison section visibility toggles.
- **Database Schema** -- 8-table schema designed for the full ha-fordpass data model (vehicle status, battery, trips, location) even though v1 only populates charging tables.
