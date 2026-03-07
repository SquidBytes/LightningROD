# Changelog

All notable changes to LightningROD are documented here.

## v0.2 -- In Progress

Session management, CSV import, UI overhaul, and data model expansion.

### Added

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
