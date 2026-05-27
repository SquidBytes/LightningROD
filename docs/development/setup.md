# Development Setup

Run LightningROD locally outside of Docker for development, with hot-reload and direct database access.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) -- fast Python package manager
- Node.js 20+ -- for Tailwind CSS / DaisyUI compilation
- Docker -- for PostgreSQL

## Steps

### 1. Install dependencies

```bash title="" 
git clone https://github.com/SquidBytes/LightningROD.git
cd LightningROD
uv sync
npm install
```

### 2. Configure environment

```bash title="" 
cp .env.example .env
```

The defaults work for local development. No changes needed unless you want different database credentials.

### 3. Start PostgreSQL

The dev compose override exposes PostgreSQL on port 5432 so you can connect with local tools:

```bash title="" 
docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml up db -d
```

### 4. Run migrations

```bash title="" 
uv run alembic upgrade head
```

### 5. Build CSS

```bash title="" 
npx @tailwindcss/cli -i input.css -o web/static/css/output.css
```

For auto-rebuild on file changes during development:

```bash title="" 
npx @tailwindcss/cli -i input.css -o web/static/css/output.css --watch
```

### 6. Import charging history (optional)

Import your own charging session data from a CSV export.

```bash title="" 
uv run python scripts/seed.py --vin YOUR_VIN_HERE
```

### 7. Start the dev server

```bash title="" 
uv run uvicorn web.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000). The server auto-reloads when you change Python files.

## Creating Migrations

When you modify a model in `db/models/`, create a migration:

```bash title="" 
# Auto-generate from model changes
uv run alembic revision --autogenerate -m "description of change"

# Apply it
uv run alembic upgrade head
```

!!! warning
    Autogenerate requires a running database to diff against. If the database isn't running, write migrations manually -- see existing migrations in `db/migrations/versions/` for examples.

## Running Tests

```bash title="" 
uv run pytest
```

## Linting

```bash title="" 
uv run ruff check .
uv run ruff format .
```

The active ruff ruleset covers `E4/E7/E9/F` (pycodestyle + pyflakes), `I` (isort), `B` (bugbear), and `UP` (pyupgrade for Python 3.11+ idioms). `B008` is excluded to allow FastAPI's `Depends`/`Form` as default-argument patterns.

## Type Checking

```bash title="" 
uv run mypy .
```

mypy is configured in `pyproject.toml` with the `pydantic.mypy` plugin. `ignore_missing_imports = true` is set so third-party stubs gaps don't block the check. Generated migration files and `.venv` are excluded.

## Connecting to the Database

With the dev compose stack running, PostgreSQL is available at `localhost:5432`:

```bash title="" 
psql -h localhost -U lightningrod -d lightningrod
```
