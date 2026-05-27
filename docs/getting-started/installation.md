# Installation

LightningROD can be deployed two ways:

- **Docker Compose** (recommended) -- two containers: the web app and PostgreSQL
- **Standalone Docker** -- single container with embedded SQLite

!!! tip "Unraid?"
    Dedicated [Unraid Setup guide](unraid.md) for Docker Compose Manager-specific steps.

## Which Docker file do I use?

Only `docker-compose.yml` lives at the repo root -- it's the default stack you get from `docker compose up`. Everything else lives under `docker/` and is opted into with an explicit `-f` flag.

### For users (deploying the app)

| File | What it is | Command |
|------|------------|---------|
| `docker-compose.yml` *(repo root)* | The default two-container stack: web app + PostgreSQL. **Start here.** | `docker compose up -d` |
| `docker/docker-compose.standalone.yml` | Compose wrapper for the single-container SQLite deployment. Reuses the same hosted image with `DATABASE_URL` pointed at `/data/lightningrod.db` and a named volume mounted at `/data`. | `docker compose -f docker/docker-compose.standalone.yml up -d` |
| `docker/Dockerfile` | Multi-stage image build recipe (Node builds CSS, Python runs the app). Mainly for local development or custom images. | `docker build -f docker/Dockerfile -t lightningrod-web:dev .` |

### For contributors (development & tests)

| File | What it is | Command |
|------|------------|---------|
| `docker/docker-compose.dev.yml` | Overlay applied **on top of** `docker-compose.yml`. Exposes PostgreSQL on `localhost:5432` so you can run the FastAPI app on your host with hot-reload. | `docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml up db -d` (see [Development Setup](../development/setup.md)) |
| `docker/docker-compose.test.yml` | Standalone test PostgreSQL on port `5433`, isolated from your dev database. Used by `./run-tests.sh`. | `docker compose -f docker/docker-compose.test.yml up -d test-db` |
| `docker/entrypoint.sh` | Web container startup script: resolves the database URL, runs Alembic migrations, then uvicorn. Baked into `docker/Dockerfile`. | *(internal)* |

## Requirements

- Docker (and Docker Compose for the two-container setup)
- A CSV export of your charging history (optional, for seeding data)

## Docker Compose

=== "Standard"

    ```bash title=""
    git clone https://github.com/SquidBytes/LightningROD.git
    cd LightningROD
    cp .env.example .env
    ```

    Edit `.env` to set a real password:

    ```bash title=".env"
    POSTGRES_USER=lightningrod
    POSTGRES_PASSWORD=your-secure-password  # (1)!
    POSTGRES_DB=lightningrod
    APP_PORT=8000
    APP_DEBUG=false
    ```

    1. Change this from the default `changeme` before running in production.

    Start the stack:

    ```bash title=""
    docker compose up -d
    ```

=== "With Reverse Proxy"

    If you're running behind a reverse proxy (Traefik, nginx, Caddy), you may want to remove the port mapping and configure your proxy to route to the container directly.

    ```bash title=""
    docker compose up -d
    ```

    Point your proxy at the `web` service on port 8000.

The app will be available at `http://localhost:8000` (or your configured `APP_PORT`).

## What Happens on Startup

The container's entrypoint script handles setup automatically:

1. Alembic runs all pending database migrations
2. Uvicorn starts the FastAPI application

The Docker build uses a multi-stage process: Node 22 compiles Tailwind CSS + DaisyUI in the first stage, then the Python 3.11 runtime stage copies the compiled CSS and starts the app.

!!! note
    The web service waits for PostgreSQL to pass its health check before starting. If the database is slow to initialize on first run, the web container will retry until it's ready.

## Verify It's Running

```bash title=""
docker compose ps
```

You should see both `db` and `web` services running. Open `http://localhost:8000` in your browser.

The database starts empty. See [Data Import](data-import.md) to load your charging history, or use the [CSV Import](../guide/csv-import.md) feature in the web UI.

## Stopping and Restarting

```bash title=""
# Stop
docker compose down

# Restart
docker compose up -d
```

Your data is stored in a named Docker volume (`pgdata`) and persists across restarts and rebuilds.

## Updating

```bash title=""
git pull
docker compose pull
docker compose up -d
```

Migrations run automatically on startup, so schema changes are applied when you update.

## Standalone Docker

Runs the application in a single container with an embedded SQLite database stored on a named Docker volume. No separate database service required.

=== "docker run"

    ```bash title=""
    git clone https://github.com/SquidBytes/LightningROD.git
    cd LightningROD
    ```

    Pull and run:

    ```bash title=""
    docker pull ghcr.io/squidbytes/lightningrod-web:latest
    docker run -d \
      -p 8000:8000 \
      -v lightningrod-data:/data \
      -e DATABASE_URL=sqlite+aiosqlite:////data/lightningrod.db \
      --name lightningrod \
      ghcr.io/squidbytes/lightningrod-web:latest
    ```

=== "docker compose (standalone)"

    ```bash title="" 
    git clone https://github.com/SquidBytes/LightningROD.git
    cd LightningROD
    cp .env.example .env
    ```
    
    !!! note
        The Postgres values in `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) are unused in standalone mode. Only `APP_PORT`, `APP_DEBUG`, and `DEMO_MODE` apply here.

    Start the standalone stack:

    ```bash title="" 
    docker compose -f docker/docker-compose.standalone.yml up -d
    ```

The app will be available at `http://localhost:8000` (or your configured `APP_PORT`).

### What Happens on Startup (Standalone)

The standalone entrypoint is the same `docker/entrypoint.sh` used by the default stack:

1. Resolves the SQLite path from `DATABASE_URL` (`/data/lightningrod.db`)
2. Ensures `/data/` exists (the Dockerfile pre-creates it and declares it as a volume)
3. Runs Alembic migrations
4. Starts the FastAPI application

!!! note
    Data is stored in a Docker volume mounted at `/data`. This persists across container restarts and rebuilds.

### Stopping and Restarting (Standalone)

```bash title="" 
# docker run
docker stop lightningrod && docker start lightningrod

# docker compose
docker compose -f docker/docker-compose.standalone.yml down
docker compose -f docker/docker-compose.standalone.yml up -d
```

### Updating (Standalone)

```bash title="" 
git pull
docker pull ghcr.io/squidbytes/lightningrod-web:latest
docker stop lightningrod && docker rm lightningrod
docker run -d \
  -p 8000:8000 \
  -v lightningrod-data:/data \
  -e DATABASE_URL=sqlite+aiosqlite:////data/lightningrod.db \
  --name lightningrod \
  ghcr.io/squidbytes/lightningrod-web:latest
```

Or with compose:

```bash title="" 
git pull
docker compose -f docker/docker-compose.standalone.yml pull
docker compose -f docker/docker-compose.standalone.yml up -d
```

## Local Image Build (Optional)

If you want to build locally, build and run a local image tag.

### Build local image

```bash title="" 
docker build -f docker/Dockerfile -t lightningrod-web:dev .
```

### Use local image with default compose (Postgres)

```bash title="" 
LIGHTNINGROD_IMAGE=lightningrod-web LIGHTNINGROD_VERSION=dev docker compose up -d
```

### Use local image with standalone compose (SQLite)

```bash title="" 
LIGHTNINGROD_IMAGE=lightningrod-web LIGHTNINGROD_VERSION=dev \
docker compose -f docker/docker-compose.standalone.yml up -d
```
