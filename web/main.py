from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db.engine import AsyncSessionLocal, engine
from web.queries.settings import seed_charger_templates
from web.routes import battery, charging, csv_import, dashboard, sessions, costs, energy, review, settings, trips


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
    # Startup: seed charger templates (idempotent)
    async with AsyncSessionLocal() as session:
        await seed_charger_templates(session)
    # Start HASS service (if configured)
    from web.services.hass_client import start_hass_service
    await start_hass_service()
    yield
    # Shutdown: stop HASS service, dispose engine
    from web.services.hass_client import hass_service
    await hass_service.stop()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="LightningROD", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="web/static"), name="static")
    app.include_router(dashboard.router)
    app.include_router(sessions.router, prefix="/charging")
    app.include_router(costs.router, prefix="/charging")
    app.include_router(energy.router, prefix="/charging")
    app.include_router(settings.router)
    app.include_router(csv_import.router)
    app.include_router(battery.router)
    app.include_router(charging.router)
    app.include_router(review.router)
    app.include_router(trips.router)

    # Register Jinja filters on all Jinja2Templates instances used by routes
    from web.unit_system import (
        convert_distance,
        convert_efficiency,
        convert_fuel_efficiency,
        convert_fuel_volume,
        convert_speed,
        convert_temp,
    )

    def _cvt(fn):
        """Wrap a converter so it returns None for None and leaves labels alone."""
        def inner(value, unit):
            return fn(value, unit) if value is not None else None
        return inner

    for route_module in [dashboard, sessions, costs, energy, settings, csv_import, charging, review, battery, trips]:
        if hasattr(route_module, "templates"):
            env = route_module.templates.env
            env.filters["localtime"] = localtime_filter
            env.filters["cvt_dist"] = _cvt(convert_distance)
            env.filters["cvt_temp"] = _cvt(convert_temp)
            env.filters["cvt_eff"] = _cvt(convert_efficiency)
            env.filters["cvt_speed"] = _cvt(convert_speed)
            env.filters["cvt_fuel_eff"] = _cvt(convert_fuel_efficiency)
            env.filters["cvt_fuel_vol"] = _cvt(convert_fuel_volume)

    return app


app = create_app()
