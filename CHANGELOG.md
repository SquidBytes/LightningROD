# Changelog

All notable changes to LightningROD are documented here.

For feature documentation, see the docs site at
<https://squidbytes.github.io/LightningROD/>.

## [Unreleased]

### Added

- Export CSV button on the Charging Sessions page — downloads the sessions
  matching the current filters (dates, charge type, network, sort)
- Delete Trip button in the trip detail drawer
- Mile-for-mile gas comparison in Savings Scenarios — fuels the distance you
  actually drove at the ICE comparison vehicle's real MPG with monthly fuel
  price history, alongside the existing energy-equivalent comparison
- Data Repair settings tab — census, dry-run preview, and one-click repair for
  historical trip data (duplicate trips, unit-corrupted distances,
  recorder-history re-enrichment), with restorable snapshots
- One-click database backup download on the Data Repair tab for SQLite
  installs (PostgreSQL installs get pg_dump instructions in the guide)

### Changed

- Trip tables and detail views show a single calculated Efficiency — the old
  "Reported" figure was the HA integration computing the identical ratio, not
  a value from the vehicle
- Dashboard network charts group networks beyond the top 7 into an "Other"
  bucket so many-network installs stay readable

### Fixed

- Battery health and degradation compared Wh-scale capacity readings against
  the rated kWh capacity (health showed absurd percentages and ignored the
  user-set gross capacity); existing rows are repaired on upgrade
- Trip driving-score radar renders a compact overall-score stat instead of a
  degenerate spike when per-category scores are unavailable
- Range Regenerated card on Driving Analytics showed "No data available" —
  regen and driving score now also ingest from the always-enabled metrics
  sensor instead of relying solely on elveh trip attributes
- CSV import duplicate detection crashed on SQLite installs (PostgreSQL-only
  SQL in the dedup query)
- AC vs DC donut (and chart-metric switches) no longer render off-center
  after switching kWh/Sessions/Cost until the window was resized
- Custom From/To date filters now work on the Costs, Battery, Charging
  Performance, Trip Sessions, and Driving Analytics pages (previously they
  silently reset the view to All-time; only Charging Sessions honored them)
- Chart tooltips no longer show full-precision floats (battery temperature,
  efficiency moving average, range recovered, gas price trends)
- Edit forms no longer show floating-point noise (15+ decimal places) in
  power, SOC, voltage, cost, and capacity fields
- Group-edit bar: choosing a Network now reliably refreshes the Location
  dropdown (a previously opened drawer could send its stale network along)
- Dashboard Charging Efficiency card now shows utilization for EVSE-mapped
  sessions even when no wall-metered energy exists (loss reads N/A with a
  hint instead of hiding the whole card)
- Duplicate trip entries with incorrect distances for vehicles set to imperial
  display units on a metric Home Assistant install
- Trip duration not populating on trip lists and detail views
- Manual trip entry stored its duration 60× too short
- Trip start time now recorded for trips ingested from Home Assistant

### Removed

## [0.4.0] - 2026-05-25

### Added

- **Cost Explorer card** on the Costs page — interactive, view of prices and total spent. Replaces the old Actual/Estimated, Cost card & view.
  by Network, Savings vs Network, and Subscription Savings sections.
  - Multi-select network filter
  - Free-charging "what-if"
  - Reference-rate comparison (pick a network or enter a custom $/kWh)
  - Per-network ledger with Δ vs reference 
  - Subscription "With / Without / Net saved" readout
- #36 - Settings → **Fuel** tab — multi-ICE-vehicle table (mark one as default), gas price history, and a fuel price trend chart. Gas/ICE configuration relocated out of General.
- Settings → **Data Sources** tab — register and configure HA-FordPass and HA gas-price sources
- Battery page **HV Pack Telemetry** card — temperature, voltage, amperage, and power, each with a 7-day sparkline.
- Battery page **Battery Temperature Over Time** chart with battery (solid) + outside-air (dashed) traces and charging-region overlays.
- Trip drawer fields populated: odometer start/end, duration, and regen recovered (I hope)
- Trip score column rendered as a color-banded radial-progress gauge.
- Column-header sort on `/driving/sessions` (replaces the toolbar).
- Demo Site and SQLite deployment option
- Backend changes for data source abstraction
- Backend changes for easier de-duplication (`uuid5`)

### Changed

- Docker deployments pull from GHCR images instead of needing to be built locally
  - Documentation updated
- SQLite is the default for docker/docker-compose.standalone.yml
  - Docker compose still uses Postgres.
    - The `DATABASE_URL` in `.env` is now an optional override allowing for external database connection
- Settings tabs restructured
  - **General** is now the default
  - Import CSV now labeled **Import**
    - Still working on fixes for CSV imports
  - Data Sources tab adjusted for future data source options
    - Large backend changes for data source adapters to make alternative vehicle support easier in the future
- Gas prices and ICE fuel-economy / tank-capacity are stored in metric and converted for display
- `/battery` charge curve defaults to the most recent session of any type. 
  - AC charging sessions y-axis caps at 25 kW
  - DC reference curve hidden (when AC session selected, DC session remain the same)
- `uuid5` used for Trip ingestion to cut down on duplicates (hopefully) 
- Timezone dropdowns in Settings and CSV Import are now searchable with regional filters.
- The three summary cards on the Costs page are folded into a Total spend and Cost ratios strip atop the Cost Explorer card.
- The Savings Scenarios card now compares your all-in EV cost (energy plus subscription fees) against gas, with the energy-only figure shown beneath.

### Fixed

- #16 - Charging sessions correctly auto-inherit the network from a resolved location when the location has one.
- Docker entrypoint auto-recovers v0.3.x databases stamped at squashed-away revisions. 
  - Non-Docker upgraders: see `db/migrations/README.md`.
- Datetimes correctly use configured timezone (converted to UTC on storage) and render based on configured settings.
- Charge-curve session picker no longer scrolls the page back to the top — only the curve card swaps.
- Free-charging what-if rebill now applies to every selected network, not just the last one ticked.

### Removed

- `/battery` standalone 12V Battery Voltage Trend chart, the Range summary card, and the 12V Battery summary card.
- `/driving/performance` regen-as-percent-of-SOC card.

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
