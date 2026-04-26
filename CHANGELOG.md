# Changelog

All notable changes to LightningROD are documented here.

For feature documentation, see the docs site at
<https://squidbytes.github.io/LightningROD/>.

## [Unreleased]

### Added

### Changed

### Fixed

### Removed

## [0.3.21] - 2026-04-25

### Added

- `ha_fordpass` adapter with `FIELD_CONTRACTS` — single ingestion entry point replacing scattered `hass_processor` / `hass_client` logic.
- Unit-detection layer: resolves HA signal units at ingestion time via a pure `to_metric()` conversion function and `UnknownSourceUnit` sentinel.
- `ingest_schema_version` column on `ev_session`, `ev_trip_metrics`, and related models.
- `/admin/data-sources` page listing all HA signal sources with contract details, contract/detection coverage, and raw last-seen values.
- Developer Tools toggle in Settings → General; Data Sources sidebar link is hidden unless developer mode is enabled.
- `gen_data_sources_doc.py` generator and committed `data-sources.md` reference doc.
- `FieldContract` module for observable-contract validation and display-layer assertions.
- mypy static type checking added to the dev toolchain (CI-enforced).
- Ruff ruleset expanded (I, B, UP) with pre-commit hook; codebase-wide type fixes applied.
- Cross-source match-and-enrich tests for `ev_trip_metrics` dedup.
- Thermal field wiring (`elvehcharging`/`outsidetemp`) for trip ingestion.
- Pre-commit reference-checking script under `scripts/`.
- `/healthz` liveness + readiness endpoint (returns 503 if the database is unreachable) and `HEALTHCHECK` directives on both Docker images so `docker ps` reports container health.
- About card on Settings → General showing the running LightningROD version with links to release notes, documentation, and source.

### Changed

- `hass_processor` and `hass_client` migrated into `ha_fordpass` adapter; legacy `_fordpass_*` feature flags removed.
- Action buttons on review location rows clustered per row (UX polish).
- Approved-tab edit dialog hoisted to page-scope modal (reuses shared `edit-loc-modal`).
- Docker artifacts moved into a dedicated `docker/` subfolder.

### Fixed

- Review queue D-B3 filter dropped on POST-merge Pending branches (WR-01 regression).
- `tripRangeRegeneration` field name corrected to `tripRangeRegenerated`.

## [0.2.13] - 2026-03-07

Session management, CSV import, UI overhaul, and Home Assistant integration.

### Added

- Home Assistant WebSocket ingestion via `ha-fordpass` — 29 sensors, auto-reconnect, 30-day backfill.
- Session CRUD (add, edit, delete) from the web UI with data-source badges.
- CSV import with auto-detection, inline error editing, and timezone-aware parsing.
- Dashboard with summary cards and monthly-cost / network / efficiency charts.
- Network management as first-class entities with color badges and per-location cost overrides.
- Cost cascade: session → location → network → subscription rate.
- EVSE telemetry on sessions (voltage, amperage, kW, charging loss %).
- Charger stall definitions per location, with network-level templates.
- User timezone setting applied to all displayed timestamps.
- Click-to-sort columns, multi-select network filter, per-page size selector.

### Changed

- Migrated UI from hand-rolled Tailwind to DaisyUI v5 throughout.
- Multi-stage Docker build with Node 22 for Tailwind v4 + DaisyUI compilation.
- HTMX and Plotly vendored as static assets (no CDN).
- Session drawer reorganised with cost breakdown card and EVSE section.
- Database schema extended: `ev_charger_stalls`, EVSE columns on sessions, `cost_per_kwh` on locations, network colours.

## [0.1.5] - 2026-02-28

Initial release.

### Added

- Docker Compose stack: PostgreSQL 16, FastAPI, Alembic, Jinja2 + HTMX + Tailwind UI.
- CSV-to-PostgreSQL import with idempotent upsert and AC/DC classification.
- Paginated session list with date-range, charge-type, and location filters.
- Slide-out session drawer with prev/next navigation.
- Configurable per-location electricity rates.
- Gas vehicle and network rate comparisons on the cost dashboard.
- Energy dashboard: lifetime kWh, efficiency trend, regen totals.
- Settings: rates, gas comparison params, unit preferences.
- 8-table schema designed for the full ha-fordpass data model.
