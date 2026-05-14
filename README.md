# LightningROD

Self-hosted charge logs, and vehicle analytics. Track charging sessions, trips, costs, and battery metrics from a web dashboard.

Built for the Ford F-150 Lightning, but should work with other Ford EVs.  
If you'd like support for a vehicle that isn't covered, open an issue and let me know.

Designed to support automatic data ingestion from Home Assistant via [ha-fordpass](https://github.com/marq24/ha-fordpass).


### **DEMO**

An interactive read-only demo is available. Data values are random and meant to showcase various features - their values may be incorrect.

**Site**: https://lightningrod.onrender.com  
*Note: The service may need time to wake up.*

> [!NOTE]
> **Personal project, built with AI help.** I work on this when I have free time and use AI (primarily Claude) as a tool and to learn new things. For more information read my [AI Usage Disclaimer](https://squidbytes.github.io/LightningROD/ai-disclaimer/).

If you find LightningROD useful, and would like to, you can [buy me a coffee](https://www.buymeacoffee.com/SquidBytes):

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/SquidBytes)

> [!IMPORTANT]
> This is a work in progress. Alternative backups are recommended.
> 
## Features

- **Charging analytics** — session CRUD, network/location/stall management, cost hierarchy (location override → network → subscription), EVSE loss and utilization metrics.
- **Battery & trip analytics** — SOC timeline, charge curves with reference overlay, estimated degradation (based on reported capacity and milage), HV pack telemetry, trip-by-trip efficiency and regen.
- **Home Assistant ingestion** — real-time WebSocket capture, auto-detected charging sessions and history backfill
- **CSV import and manual entry** — for data outside Home Assistant (work in progress, not recommended currently)

Full feature list and screenshots in the [documentation site](https://squidbytes.github.io/LightningROD/).

## What's Next

The short version: more ways to get data in, like EVSE-direct (Ford Charge Station Pro, OCPP), different import formats (XLSX, network app exports), long term stretch goal is to support data ingestion through OBD readers not relying upon the API.

## Documentation

Full documentation is available at the [documentation site](https://SquidBytes.github.io/LightningROD/).

- [Installation](https://squidbytes.github.io/LightningROD/getting-started/installation/) -- Docker Compose setup and startup
- [Configuration](https://squidbytes.github.io/LightningROD/getting-started/configuration/) -- Environment variables and in-app settings
- [Data Import](https://squidbytes.github.io/LightningROD/getting-started/data-import/) -- CSV format, seed script, classification rules
- [Home Assistant](https://squidbytes.github.io/LightningROD/guide/home-assistant/) -- Real-time FordPass data ingestion via WebSocket
- [Development](https://squidbytes.github.io/LightningROD/development/setup/) -- Running outside of the Docker enviornment with reloading and database access
- [Architecture](https://squidbytes.github.io/LightningROD/development/architecture/) -- Project structure and patterns
- [Database](https://squidbytes.github.io/LightningROD/development/database/) -- Schema, models, migrations

## Acknowledgments

- [ha-fordpass](https://github.com/marq24/ha_fordpass) by marq24 -- Home Assistant integration for Ford vehicles
- [fordpass-ha](https://github.com/itchannel/fordpass-ha) by itchannel -- Home Assistant integration that started this journey
- [TeslaMate](https://github.com/teslamate-org/teslamate) -- Inspiration for the project concept


## Gallery

Selected views from the current release.

### Overview

![overview](docs/assets/images/lr_overview.gif)

### Session List and drawer

![sessionList](docs/assets/images/lr_sessions.gif)

### Cost Page

![cost page](docs/assets/images/lr_costs.gif)

### Settings Page

![main settings page](docs/assets/images/lr_settings.gif)


## Quick Start

### Docker Compose (Postgres):

```bash
git clone https://github.com/SquidBytes/LightningROD.git
cd LightningROD
cp .env.example .env   # set POSTGRES_PASSWORD
docker compose up -d
```

The app is at `http://localhost:8000`.

### Standalone Docker (SQLite)

Pre-built images are published to GitHub Container Registry on each release. The simplest single-container setup uses SQLite on a named volume:

```bash
docker run -d \
  -p 8000:8000 \
  -v lightningrod-data:/data \
  -e DATABASE_URL=sqlite+aiosqlite:////data/lightningrod.db \
  --name lightningrod \
  ghcr.io/squidbytes/lightningrod-web:latest
```


Full install options like Unraid, external databases, etc can be found in the [Installation guide](https://squidbytes.github.io/LightningROD/getting-started/installation/).
