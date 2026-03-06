from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db.engine import AsyncSessionLocal, engine
from web.queries.settings import seed_charger_templates, seed_predefined_networks
from web.routes import csv_import, dashboard, sessions, costs, energy, settings


def localtime_filter(dt, tz_str: str = "UTC", fmt: str | None = None):
    """Convert a UTC datetime to the given timezone.

    Args:
        dt: A datetime object (assumed UTC if naive).
        tz_str: IANA timezone string (e.g. 'America/New_York').
        fmt: Optional strftime format string. If provided, returns formatted
             string; otherwise returns the converted datetime object.

    Returns:
        Formatted string if fmt is given, converted datetime otherwise.
        Returns empty string for None input.
    """
    if dt is None:
        return "" if fmt else None
    if not isinstance(dt, datetime):
        return dt
    # Ensure the datetime is timezone-aware (assume UTC if naive)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        converted = dt.astimezone(ZoneInfo(tz_str))
    except (KeyError, Exception):
        converted = dt  # Fall back to original if invalid tz
    if fmt:
        return converted.strftime(fmt)
    return converted


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed predefined networks (idempotent — skips existing by name)
    async with AsyncSessionLocal() as session:
        await seed_predefined_networks(session)
        await seed_charger_templates(session)
    yield
    # Shutdown: dispose engine connections
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="LightningROD", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="web/static"), name="static")
    app.include_router(dashboard.router)
    app.include_router(sessions.router)
    app.include_router(costs.router)
    app.include_router(energy.router)
    app.include_router(settings.router)
    app.include_router(csv_import.router)

    # Register localtime filter on all Jinja2Templates instances used by routes
    for route_module in [dashboard, sessions, costs, energy, settings, csv_import]:
        if hasattr(route_module, "templates"):
            route_module.templates.env.filters["localtime"] = localtime_filter

    return app


app = create_app()
