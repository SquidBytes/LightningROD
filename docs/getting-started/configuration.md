# Configuration

LightningROD is configured through environment variables and in-app settings.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | `lightningrod` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `changeme` | PostgreSQL password |
| `POSTGRES_DB` | `lightningrod` | Database name |
| `POSTGRES_HOST` | `localhost` | Database host. Set to `db` when using Docker Compose (handled automatically). |
| `APP_PORT` | `8000` | Port the web UI is served on |
| `DEBUG` | `false` | Enable debug logging and SQL echo |

## Example

```bash title=".env.example"
--8<-- ".env.example"
```

!!! warning
    Change `POSTGRES_PASSWORD` from the default before running in production. The default value `changeme` is only suitable for local development.

## How Configuration is Loaded

The application uses pydantic-settings to read environment variables. The configuration class assembles the async database URL from the individual PostgreSQL variables:

```python title="config.py" linenums="1"
--8<-- "config.py"
```

## Docker Compose Override

When running with Docker Compose, the `POSTGRES_HOST` is automatically set to `db` (the service name) via the `environment` section in `docker-compose.yml`:

```yaml title="docker-compose.yml" hl_lines="6-7"
  web:
    build:
      context: .
      dockerfile: Dockerfile
    env_file: .env
    environment:
      - POSTGRES_HOST=db
```

You do not need to change `POSTGRES_HOST` in your `.env` file when using Docker Compose.

## In-App Settings

Settings configured through the web UI at `/settings`:

| Setting | Storage | Description |
|---------|---------|-------------|
| Charging networks | `ev_charging_networks` table | Per-network electricity costs and colors |
| Locations | `ev_location_lookup` table | Named locations with optional cost override |
| Charger stalls | `ev_charger_stalls` table | Charger specs per location |
| Gas comparison | `app_settings` | MPG and gas price for savings calculations |
| Unit preferences | `app_settings` | US (mi/kWh) or EU (km/kWh) |
| Timezone | `app_settings` | Display timezone (e.g., America/New_York) |
| Comparison toggles | `app_settings` | Show or hide cost comparison sections |

These are managed at `/settings` and stored in the database.
