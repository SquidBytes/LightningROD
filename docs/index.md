# LightningROD

Self-hosted charging analytics for Ford electric vehicles.

Track charging sessions, analyze costs, and monitor energy consumption with a web-based dashboard. Built for the Ford F-150 Lightning, designed to work with any Ford EV using data from [ha-fordpass](https://github.com/marq24/ha_fordpass) or CSV imports.

<!-- TODO: Add screenshot -->
<!-- ![Dashboard](assets/screenshot-dashboard.png) -->

---

## Features

:lucide-battery-charging: **Charging Sessions** -- Browse your complete charging history with filters for date range, charge type (AC/DC), and location. Each session expands into a detail view with all available fields.

:lucide-dollar-sign: **Cost Analytics** -- Configure network costs per location, see lifetime spending by network, and compare what you would have paid at different network costs or with a gas vehicle.

:lucide-gauge: **Energy Dashboard** -- Track total energy consumed, view efficiency trends over time, and see regenerative braking totals.

:lucide-sliders-horizontal: **Settings** -- Manage charging networks, gas comparison parameters, US/EU unit preferences, and comparison section visibility.

---

## Quick Start

```bash
git clone https://github.com/yourusername/LightningROD.git
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
| Frontend | HTMX, Tailwind CSS, Plotly |
| Deployment | Docker Compose |
