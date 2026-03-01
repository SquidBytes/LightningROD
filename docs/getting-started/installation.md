# :lucide-download: Installation

LightningROD runs as a Docker Compose stack with two services: the web application and PostgreSQL.

## Requirements

- Docker and Docker Compose
- A CSV export of your charging history (optional, for seeding data)

## Docker Compose

=== "Standard"

    ```bash
    git clone https://github.com/yourusername/LightningROD.git
    cd LightningROD
    cp .env.example .env
    ```

    Edit `.env` to set a real password:

    ```bash title=".env"
    POSTGRES_USER=lightningrod
    POSTGRES_PASSWORD=your-secure-password  # (1)!
    POSTGRES_DB=lightningrod
    POSTGRES_HOST=localhost
    APP_PORT=8000
    DEBUG=false
    ```

    1. Change this from the default `changeme` before running in production.

    Start the stack:

    ```bash
    docker compose up --build -d
    ```

=== "With Reverse Proxy"

    If you're running behind a reverse proxy (Traefik, nginx, Caddy), you may want to remove the port mapping and configure your proxy to route to the container directly.

    ```bash
    docker compose up --build -d
    ```

    Point your proxy at the `web` service on port 8000.

The app will be available at `http://localhost:8000` (or your configured `APP_PORT`).

## What Happens on Startup

The container's entrypoint script handles setup automatically:

```bash title="entrypoint.sh"
--8<-- "entrypoint.sh"
```

1. Alembic runs all pending database migrations
2. Uvicorn starts the FastAPI application

!!! note
    The web service waits for PostgreSQL to pass its health check before starting. If the database is slow to initialize on first run, the web container will retry until it's ready.

## Verify It's Running

```bash
docker compose ps
```

You should see both `db` and `web` services running. Open `http://localhost:8000` in your browser.

The database starts empty. See [Data Import](data-import.md) to load your charging history.

## Stopping and Restarting

```bash
# Stop
docker compose down

# Restart
docker compose up -d
```

Your data is stored in a named Docker volume (`pgdata`) and persists across restarts and rebuilds.

## Updating

```bash
git pull
docker compose up --build -d
```

Migrations run automatically on startup, so schema changes are applied when you update.
