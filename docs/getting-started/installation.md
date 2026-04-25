# Installation

LightningROD can be deployed two ways:

- **Docker Compose** (recommended) -- two containers: the web app and PostgreSQL
- **Standalone Docker** -- single container with embedded PostgreSQL

!!! tip "Unraid?"
    Dedicated [Unraid Setup guide](unraid.md) for Docker Compose Manager-specific steps.

## Which Docker file do I use?

Only `docker-compose.yml` lives at the repo root -- it's the default stack you get from `docker compose up`. Everything else lives under `docker/` and is opted into with an explicit `-f` flag (or by building from a non-default `Dockerfile`).

### For users (deploying the app)

| File | What it is | Command |
|------|------------|---------|
| `docker-compose.yml` *(repo root)* | The default two-container stack: web app + PostgreSQL. **Start here.** | `docker compose up --build -d` |
| `docker/Dockerfile` | Multi-stage image for the web container (Node builds CSS, Python runs the app). Referenced by `docker-compose.yml` -- you don't invoke it directly. | *(built automatically)* |
| `docker/Dockerfile.standalone` | All-in-one image: app **and** PostgreSQL in a single container. For users who don't want a separate db container. | `docker build -f docker/Dockerfile.standalone -t lightningrod:standalone .` |
| `docker/docker-compose.standalone.yml` | Compose wrapper that builds and runs the standalone image with a named volume. Easier than `docker run` if you prefer Compose. | `docker compose -f docker/docker-compose.standalone.yml up --build -d` |

### For contributors (development & tests)

| File | What it is | Command |
|------|------------|---------|
| `docker/docker-compose.dev.yml` | Overlay applied **on top of** `docker-compose.yml`. Exposes PostgreSQL on `localhost:5432` so you can run the FastAPI app on your host with hot-reload. | `docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml up db -d` (see [Development Setup](../development/setup.md)) |
| `docker/docker-compose.test.yml` | Standalone test PostgreSQL on port `5433`, isolated from your dev database. Used by `./run-tests.sh`. | `docker compose -f docker/docker-compose.test.yml up -d test-db` |
| `docker/entrypoint.sh` | Web container startup script: runs Alembic migrations, then uvicorn. Baked into `docker/Dockerfile`. | *(internal)* |
| `docker/entrypoint.standalone.sh` | Standalone container startup: bootstraps PostgreSQL, creates the role/db, runs migrations, starts the app. Baked into `docker/Dockerfile.standalone`. | *(internal)* |

## Requirements

- Docker (and Docker Compose for the two-container setup)
- A CSV export of your charging history (optional, for seeding data)

## Docker Compose

=== "Standard"

    ```bash
    git clone https://github.com/SquidBytes/LightningROD.git
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

1. Alembic runs all pending database migrations
2. Uvicorn starts the FastAPI application

The Docker build uses a multi-stage process: Node 22 compiles Tailwind CSS + DaisyUI in the first stage, then the Python 3.11 runtime stage copies the compiled CSS and starts the app.

!!! note
    The web service waits for PostgreSQL to pass its health check before starting. If the database is slow to initialize on first run, the web container will retry until it's ready.

## Verify It's Running

```bash
docker compose ps
```

You should see both `db` and `web` services running. Open `http://localhost:8000` in your browser.

The database starts empty. See [Data Import](data-import.md) to load your charging history, or use the [CSV Import](../guide/csv-import.md) feature in the web UI.

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

## Standalone Docker

Runs both the application and PostgreSQL in a single container.

=== "docker run"

    ```bash
    git clone https://github.com/SquidBytes/LightningROD.git
    cd LightningROD
    cp .env.example .env
    ```

    Edit `.env` to set a real password, then build and run:

    ```bash
    docker build -f docker/Dockerfile.standalone -t lightningrod:standalone .
    docker run -d \
      -p 8000:8000 \
      -v lightningrod-data:/var/lib/postgresql/data \
      --env-file .env \
      --name lightningrod \
      lightningrod:standalone
    ```

=== "docker compose (standalone)"

    ```bash
    git clone https://github.com/SquidBytes/LightningROD.git
    cd LightningROD
    cp .env.example .env
    ```

    Edit `.env` to set a real password, then start:

    ```bash
    docker compose -f docker/docker-compose.standalone.yml up --build -d
    ```

The app will be available at `http://localhost:8000` (or your configured `APP_PORT`).

### What Happens on Startup (Standalone)

The standalone entrypoint handles everything in a single container:

1. Initializes the PostgreSQL data directory if empty (first run)
2. Starts PostgreSQL as a background service
3. Creates the database role and database if they don't exist
4. Runs Alembic migrations
5. Starts the FastAPI application

!!! note
    Data is stored in a Docker volume mounted at `/var/lib/postgresql/data`. This persists across container restarts and rebuilds.

### Stopping and Restarting (Standalone)

```bash
# docker run
docker stop lightningrod && docker start lightningrod

# docker compose
docker compose -f docker/docker-compose.standalone.yml down
docker compose -f docker/docker-compose.standalone.yml up -d
```

### Updating (Standalone)

```bash
git pull
docker build -f docker/Dockerfile.standalone -t lightningrod:standalone .
docker stop lightningrod && docker rm lightningrod
docker run -d \
  -p 8000:8000 \
  -v lightningrod-data:/var/lib/postgresql/data \
  --env-file .env \
  --name lightningrod \
  lightningrod:standalone
```

Or with compose:

```bash
git pull
docker compose -f docker/docker-compose.standalone.yml up --build -d
```
