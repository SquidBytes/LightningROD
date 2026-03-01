# :lucide-settings: Configuration

LightningROD is configured through environment variables, loaded from a `.env` file.

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

Some settings are configured through the web UI rather than environment variables:

- **Charging networks** -- Per-network electricity costs ($/kWh)
- **Gas comparison** -- MPG and gas price for savings calculations
- **Unit preferences** -- US (mi/kWh) or EU (km/kWh)
- **Comparison toggles** -- Show or hide cost comparison sections

These are stored in the `app_settings` database table and managed at `/settings`.
