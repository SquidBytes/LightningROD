# :lucide-code: Development Setup

Run LightningROD locally outside of Docker for development, with hot-reload and direct database access.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) -- fast Python package manager
- Docker -- for PostgreSQL

## Steps

### 1. Install dependencies

```bash
git clone https://github.com/yourusername/LightningROD.git
cd LightningROD
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

The defaults work for local development. No changes needed unless you want different database credentials.

### 3. Start PostgreSQL

The dev compose override exposes PostgreSQL on port 5432 so you can connect with local tools, and keeps the web service from starting (you'll run it locally instead):

```yaml title="docker-compose.dev.yml"
--8<-- "docker-compose.dev.yml"
```

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up db -d
```

### 4. Run migrations

```bash
uv run alembic upgrade head
```

### 5. Seed data (optional)

```bash
uv run python scripts/seed.py --vin YOUR_VIN_HERE
```

### 6. Start the dev server

```bash
uv run uvicorn web.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000). The server auto-reloads when you change Python files.

## Creating Migrations

When you modify a model in `db/models/`, create a migration:

```bash
# Auto-generate from model changes
uv run alembic revision --autogenerate -m "description of change"

# Apply it
uv run alembic upgrade head
```

!!! warning
    Autogenerate requires a running database to diff against. If the database isn't running, you can write migrations manually -- see the existing migrations in `db/migrations/versions/` for examples.

## Running Tests

```bash
uv run pytest
```

## Linting

```bash
uv run ruff check .
uv run ruff format .
```

## Connecting to the Database

With the dev compose stack running, PostgreSQL is available at `localhost:5432`:

```bash
# psql
psql -h localhost -U lightningrod -d lightningrod

# Or use any GUI tool (pgAdmin, DBeaver, etc.)
```
