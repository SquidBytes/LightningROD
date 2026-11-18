# LightningROD

**Self-hosted** charging and vehicle analytics.

Developed using my own Ford F-150 Lightning. Works with other Ford EVs too.

Track charging sessions, analyze costs, and monitor energy consumption with a web-based dashboard.

!!! tip "Try the demo"
    A read-only interactive demo is available: [lightningrod.dev/demo](https://lightningrod.dev/demo)

    The data is randomly generated, so the values may not add up. The service may need a moment to wake up.

!!! info "AI Disclaimer"
    AI (primarily Claude) was used.

    Read my [AI Usage Disclaimer](ai-disclaimer.md) for more information.

    For how it all started, read the [Project History](history.md).

---

## Data Sources

Pick whichever fits your setup. You can use more than one.

- **[Home Assistant](guide/home-assistant.md)** -- Reads live data from [ha-fordpass](https://github.com/marq24/ha-fordpass) and detects charging sessions automatically.
- **[FordConnect](guide/fordconnect.md)** -- Reads straight from Ford's API, no Home Assistant needed. Only North America has been tested.
- **[CSV import](guide/csv-import.md)** -- Upload charging sessions from a spreadsheet. Work in progress.
- **[Manual entry](guide/sessions.md)** -- Add or edit a session by hand from the Charging Sessions page.

## Features

- **[Dashboard](guide/dashboard.md)** -- All your vehicles, lifetime totals, and energy by network.
- **[Charging Sessions](guide/sessions.md)** -- Sort, filter, add, edit, and export sessions. Open one to see its cost and charge curve.
- **[Charging Performance](guide/performance.md)** -- AC vs DC energy, charging efficiency over time, and an estimated DC charge curve.
- **[Cost Analytics](guide/costs.md)** -- What you paid per network, subscription savings, and comparisons against gas or other networks.
- **[Battery Analytics](guide/battery.md)** -- State of charge, pack temperature, charge curves, and estimated degradation.
- **[Driving](guide/driving.md)** -- Trip list with drive graphs, plus efficiency vs temperature and regen.
- **[Review Queue](guide/review-queue.md)** -- Verify, edit, or merge the networks and locations LightningROD picks up.
- **[Settings](guide/settings.md)** -- Vehicles, networks and locations, gas prices, units, and data sources.
- **[Data Repair](guide/data-repair.md)** -- Fixes old trip data damaged by bugs that have since been patched. Preview every change first. Each repair takes a snapshot you can restore.

---

## Quick Start

```bash title="" 
git clone https://github.com/SquidBytes/LightningROD.git
cd LightningROD
cp .env.example .env
docker compose up -d
```

The app will be available at `http://localhost:8000`. See [Installation](getting-started/installation.md) for full details.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.14 |
| Web framework | FastAPI |
| Database | PostgreSQL 16 or SQLite |
| ORM | SQLAlchemy 2.1 (async) |
| Migrations | Alembic |
| Templates | Jinja2 |
| Frontend | HTMX 2.0, Tailwind CSS v4, DaisyUI v5, Plotly |
| Deployment | Docker Compose |
| Docs | [Zensical](https://zensical.org/) |

---

LightningROD is not affiliated with Ford Motor Company.
