<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/logo-lockup-dark.svg">
  <img src="website/logo-lockup-light.svg" alt="LightningROD" width="196">
</picture>

## Self-hosted charging and vehicle analytics.

**Developed using my own Ford F-150 Lightning. Works with other Ford EVs too.**

LightningROD collects your vehicle data automatically, so you don't need to keep spreadsheets.

> If you'd like support for a vehicle that isn't covered, open an issue and let me know.

### **DEMO**

An interactive read-only demo is available. The data is random, so values may not add up.

**Site**: https://lightningrod.dev/demo  
*Note: The service may need time to wake up.*

> [!NOTE]
> **Personal project, built with AI help.** I work on this in my free time and use AI (primarily Claude) as a tool. Read the [AI Usage Disclaimer](https://docs.lightningrod.dev/ai-disclaimer/) for more information.

If you find LightningROD useful, and would like to, you can [buy me a coffee](https://www.buymeacoffee.com/SquidBytes):

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/SquidBytes)

> [!IMPORTANT]
> This is a work in progress. Alternative backups are recommended.

## Features

- **Charging** -- sessions, costs, and networks.
- **Battery** -- state of charge, charge curves, and pack telemetry.
- **Trips** -- trip history, efficiency, and regen.

The full feature list is on the [documentation site](https://docs.lightningrod.dev/).

## Data Sources

Pick whichever fits your setup. You can use more than one.

- **[Home Assistant](https://docs.lightningrod.dev/guide/home-assistant/)** -- Reads live data from [ha-fordpass](https://github.com/marq24/ha-fordpass) and detects charging sessions automatically. Best if you already run Home Assistant.
- **[FordConnect](https://docs.lightningrod.dev/guide/fordconnect/)** -- Reads straight from Ford's API, no Home Assistant needed. Requires a Ford developer account, which can take a few days to be approved. Only North America has been tested.
- **[CSV import](https://docs.lightningrod.dev/guide/csv-import/)** -- Upload charging sessions from a spreadsheet. Work in progress.
- **[Manual entry](https://docs.lightningrod.dev/guide/sessions/)** -- Add or edit a session by hand from the Charging Sessions page.

## What's Next

More ways to get data in: EVSE-direct (Ford Charge Station Pro, OCPP) and more import formats (XLSX, network app exports). Long term, OBD readers, so the API isn't required.

## Quick Start

### Docker Compose (Postgres):

```bash
git clone https://github.com/SquidBytes/LightningROD.git
cd LightningROD
cp .env.example .env   # set POSTGRES_PASSWORD
docker compose up -d
```

The app runs at `http://localhost:8000`.

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

Full installation options like Unraid and external databases are in the [Installation guide](https://docs.lightningrod.dev/getting-started/installation/).

## Documentation

Everything else is on the [documentation site](https://docs.lightningrod.dev/). Environment variables and in-app settings are covered in [Configuration](https://docs.lightningrod.dev/getting-started/configuration/).

## Acknowledgments

- [ha-fordpass](https://github.com/marq24/ha-fordpass) by marq24 -- Home Assistant integration for Ford vehicles
- [fordpass-ha](https://github.com/itchannel/fordpass-ha) by itchannel -- Home Assistant integration that started this journey
- [TeslaMate](https://github.com/teslamate-org/teslamate) -- Inspiration for the project concept

## License

[AGPL-3.0](LICENSE). Not affiliated with Ford Motor Company.
