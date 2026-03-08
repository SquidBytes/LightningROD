# LightningROD

Self-hosted charging analytics for Ford electric vehicles. Track charging sessions, analyze costs, and monitor energy consumption with a web-based dashboard.

Built for the Ford F-150 Lightning, but should work with any Ford EV.

> [!IMPORTANT]
> This is a work in progress. Do not use this as the only data storage.

Supports automatic data ingestion from Home Assistant via [ha-fordpass](https://github.com/marq24/ha_fordpass), CSV import, and manual entry.

> [!NOTE]
> **This is my own personal project**
> I am using it for a fun side project, and for learning.

"The goal is to make this adaptable for different users and data types, but much of it is tailored to my specific data and storage methods."

If you would like to, please consider buying me a coffee.

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/SquidBytes)

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

Screenshots are from `v0.1.5` and may not be up to date

### Session List and drawer

![sessionList v0.2](docs/assets/images/lr_sessions.gif)


### Cost Page

![cost page v0.2](docs/assets/images/lr_costs.gif)

### Energy Page

![energy v0.2](docs/assets/images/lr_energy.gif)

### Settings Page

![main settings page v0.2](docs/assets/images/lr_settings.gif)

![settings networks v0.2](docs/assets/images/lr_settings_networks.gif)

![settings csv import v0.2](docs/assets/images/lr_csv_import.gif)


## Quick Start

### Docker Compose (recommended)

```bash
git clone https://github.com/yourusername/LightningROD.git
cd LightningROD
cp .env.example .env
# Edit .env -- at minimum, set a real POSTGRES_PASSWORD
docker compose up --build -d
```

The app will be available at `http://localhost:8000`. Migrations run automatically on startup.

Reference the full [documentation site](https://SquidBytes.github.io/LightningROD/).
