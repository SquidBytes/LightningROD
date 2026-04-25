# Changelog

All notable changes to LightningROD are documented here.

## v0.3 -- In Progress

Ingestion overhaul, unit-detection layer, developer tools, and data model hardening.

### Added

- **`ha_fordpass` adapter + `FIELD_CONTRACTS`** -- Single ingestion entry point for all Home Assistant FordPass signals. Replaces scattered unit-conversion logic that previously lived in `hass_processor` / `hass_client`. Every known HA entity/attribute pair is declared with its source unit and target field.
- **Unit-detection layer** -- Resolves HA signal units at ingestion time via a five-method priority chain: `declared` → `read_time_uom` → `device_class_ha_config` → `cross_reference` → `unknown`. Results are held in an in-memory cache for the process lifetime; no DB persistence required.
- **`/admin/data-sources` page** -- Lists every HA signal source with contract details, detection method/confidence, coverage status, and last raw value seen. Useful for diagnosing unit detection issues without reading logs.
- **Developer Tools toggle** -- Settings → General now has an "Enable developer tools" checkbox. When off (default), the Data Sources nav link is hidden. When on, it appears under the System group in the sidebar.
- **`ingest_schema_version` column** -- Added to `ev_charging_session`, `ev_trip_metrics`, and `ev_battery_status` tables. Lets ingestion contracts be versioned independently of the app release.
- **mypy static type checking** -- Added to the dev toolchain (`uv run mypy .`).
- **Ruff ruleset expanded** -- Now covers `I` (isort), `B` (bugbear), and `UP` (pyupgrade) in addition to the base `E`/`F` rules. Pre-commit hook enforces ruff on commit.

### Changed

- `hass_processor` and `hass_client` migrated into the `ha_fordpass` adapter; legacy `_fordpass_*` feature flags removed.
- Action buttons on review-location rows clustered per row (UX polish).
- Approved-tab edit dialog hoisted to page-scope modal (reuses shared `edit-loc-modal`).

### Fixed

- Review queue D-B3 filter dropped on POST-merge Pending branches (WR-01 regression).
- `tripRangeRegeneration` field name corrected to `tripRangeRegenerated` (typo in upstream contract).

## v0.2 -- 2026-03-07

Session management, CSV import, UI overhaul, and data model expansion.

### Added

- **Home Assistant Integration** -- Real-time WebSocket connection to Home Assistant for automatic FordPass data ingestion. Authenticates with long-lived access token. Processes 29 FordPass sensors with unit normalization (imperial to metric). Creates charging sessions from `energytransferlogentry` events with full field extraction (energy, SOC, duration, power stats, location, charger type). Batched vehicle and battery telemetry writes. VIN auto-detection from entity IDs. Exponential backoff reconnection. 30-day history backfill via REST API. Live connection status with 10-second polling. Duplicate session detection.
- **HASS Settings** -- Home Assistant configuration tab in Settings with URL, access token (masked display), VIN override, unit system selection, and auto-connect toggle. Connection status display with event counter, error tracking, detected VIN, and unit system. Reconnect, disconnect, and backfill controls.
- **Session CRUD** -- Add, edit, and delete charging sessions from the web UI. Edit modal with three tabs (Basics/Details/Notes). Data source badges track origin (Manual Entry, Imported, HASS, Edited).
- **CSV Import** -- Template-based CSV import with auto-detection fallback. Inline error/duplicate editing with blur-triggered re-verify. Timezone-aware parsing. Three-step flow: Upload, Preview, Import.
- **Dashboard** -- Summary cards (total sessions, energy, cost, miles) plus three charts: monthly cost trend, energy by network, and efficiency trend. Charging efficiency card with aggregate loss and utilization metrics.
- **Network Management** -- Networks as first-class entities with color badges. Expandable location management per network. Per-location cost override. Charger stall configuration with rated kW, connector type, and default stall auto-selection.
- **Cost Hierarchy** -- Location cost_per_kwh overrides network cost_per_kwh. Estimated cost stored on sessions. Cost breakdown card in drawer showing actual vs estimated with difference.
- **EVSE Data** -- Charger-side fields on sessions: voltage, amperage, kW, energy, max power, rated capacity, source provenance. Charging loss (kWh and %) and utilization (%) calculated when data available.
- **Charger Stalls** -- Per-location stall definitions with charger type, rated kW, voltage, amperage, connector. Network-level charger templates for popular networks. Auto-fill EVSE fields on stall selection.
- **Timezone Support** -- User timezone setting. All timestamps displayed in local timezone. Import-time timezone selection for naive CSV timestamps.
- **Sort and Filter** -- Click-to-sort column headers with three-state cycle. Multi-select network filter with color badges. Filter chips showing active filters. Per-page size selector (25/50/100).

### Changed

- **UI Component Library** -- Migrated from hand-rolled Tailwind components to DaisyUI v5. All modals, drawers, tables, tabs, badges, cards, and form controls now use DaisyUI classes.
- **CSS Build** -- Multi-stage Docker build with Node 22 for Tailwind v4 + DaisyUI compilation. HTMX and Plotly vendored as static assets (no CDN).
- **Filter Bar** -- Shared compact date-range filter bar across sessions, costs, and energy pages. Pill-style preset buttons with active state.
- **Session Drawer** -- Reorganized with cost breakdown card, EVSE/Charger section, and network color badges.
- **Database Schema** -- Added `ev_charger_stalls` table. Added EVSE columns, `estimated_cost`, `stall_id` to sessions. Added `cost_per_kwh` to locations. Added `color` to networks.

## v0.1.5 -- 2026-02-28

Initial release. Core charging analytics platform.

### Added

- **Infrastructure** -- Docker Compose stack with PostgreSQL 16, FastAPI, Alembic auto-migrations, and a dark-mode web UI (Jinja2 + HTMX + Tailwind).
- **Data Seed** -- CSV-to-PostgreSQL import script with idempotent upsert, automatic AC/DC classification, and location type assignment.
- **Charging Sessions** -- Paginated session list with filters for date range (presets and custom), charge type, and location. Slide-out detail drawer with all 30 session fields and prev/next navigation.
- **Cost Analytics** -- Configurable per-network charging costs. Lifetime cost summary with free vs. paid breakdown. Gas vehicle comparison and network cost comparison with toggleable sections.
- **Energy Dashboard** -- Total lifetime energy consumed, efficiency trend chart with rolling average, regenerative braking totals (when data available), and configurable US/EU unit display.
- **Settings** -- Network cost management, gas comparison parameters (MPG, $/gallon), unit preferences, and comparison section visibility toggles.
- **Database Schema** -- 8-table schema designed for the full ha-fordpass data model (vehicle status, battery, trips, location) even though v1 only populates charging tables.
