"""FastAPI application factory, routes, filters, and lifespan hooks."""

import os
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text


def _resolve_version() -> str:
    """Report the running version.

    Preference order:
    1. LIGHTNINGROD_VERSION env (baked in by Docker build arg)
    2. pyproject-installed package metadata
    3. "dev" fallback for uninstalled source runs
    """
    env_val = os.environ.get("LIGHTNINGROD_VERSION", "").strip()
    if env_val:
        return env_val
    try:
        return pkg_version("lightningrod")
    except PackageNotFoundError:
        return "dev"


APP_VERSION = _resolve_version()

from db.engine import AsyncSessionLocal, engine
from web import developer_mode
from web.queries.settings import get_app_setting, seed_charger_templates
from web.routes import (
    battery,
    charging,
    costs,
    csv_import,
    dashboard,
    driving_performance,
    locations,
    performance,
    review,
    sessions,
    settings,
    trips,
)
from web.routes.admin import data_sources as admin_data_sources
from web.tooltips import TOOLTIPS


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
        dt = dt.replace(tzinfo=UTC)
    try:
        converted = dt.astimezone(ZoneInfo(tz_str))
    except Exception:
        converted = dt  # Fall back to original if invalid tz
    if fmt:
        return converted.strftime(fmt)
    return converted


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed charger templates (idempotent) + restore developer mode flag
    async with AsyncSessionLocal() as session:
        await seed_charger_templates(session)
        val = await get_app_setting(session, "developer_mode", "false")
        developer_mode.set_enabled(val == "true")
    # Start ingestion runtimes (one per enabled data_source_configs row)
    from web.services.ingestion import supervisor
    await supervisor.start_all()
    yield
    # Shutdown: stop runtimes, dispose engine
    await supervisor.stop_all()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="LightningROD", version=APP_VERSION, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="web/static"), name="static")

    # Demo-mode write protection. Only mounted when DEMO_MODE=true at startup;
    # production deploys never register this middleware. Blocks DELETE/PUT/PATCH
    # with a 403 JSON body and lets safe methods pass through unchanged.
    if os.environ.get("DEMO_MODE", "").lower() == "true":
        from web.middleware.demo_mode import DemoModeMiddleware
        app.add_middleware(DemoModeMiddleware)

    # Lightweight version endpoint — useful for healthchecks, deploy scripts,
    # and quickly confirming which build is running behind a reverse proxy.
    @app.get("/version", include_in_schema=False)
    async def _version_endpoint() -> dict:
        return {"name": "LightningROD", "version": APP_VERSION}

    # Liveness + readiness probe. 200 if the app is up and the DB responds;
    # 503 if the DB ping fails. Suitable for Docker HEALTHCHECK,
    # uptime-kuma, and reverse-proxy health probes.
    @app.get("/healthz", include_in_schema=False)
    async def _healthz_endpoint() -> JSONResponse:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            db_status = "ok"
            status_code = 200
        except Exception:
            db_status = "error"
            status_code = 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ok" if db_status == "ok" else "degraded",
                "version": APP_VERSION,
                "db": db_status,
            },
        )
    app.include_router(dashboard.router)
    app.include_router(sessions.router, prefix="/charging")
    app.include_router(costs.router, prefix="/charging")
    app.include_router(performance.router, prefix="/charging")
    app.include_router(settings.router)
    app.include_router(csv_import.router)
    app.include_router(battery.router)
    app.include_router(charging.router)
    app.include_router(review.router)
    app.include_router(locations.router)
    app.include_router(trips.router, prefix="/driving")
    app.include_router(driving_performance.router, prefix="/driving")
    app.include_router(admin_data_sources.router)

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

    for route_module in [dashboard, sessions, costs, performance, settings, csv_import, charging, review, battery, trips, driving_performance, admin_data_sources]:
        if hasattr(route_module, "templates"):
            env = route_module.templates.env
            globals_map = cast(MutableMapping[str, Any], env.globals)
            globals_map["tooltips"] = TOOLTIPS
            globals_map["developer_mode"] = developer_mode.is_enabled
            globals_map["app_version"] = APP_VERSION
            globals_map["demo_mode"] = os.environ.get("DEMO_MODE", "").lower() == "true"
            env.filters["localtime"] = localtime_filter
            env.filters["cvt_dist"] = _cvt(convert_distance)
            env.filters["cvt_temp"] = _cvt(convert_temp)
            env.filters["cvt_eff"] = _cvt(convert_efficiency)
            env.filters["cvt_speed"] = _cvt(convert_speed)
            env.filters["cvt_fuel_eff"] = _cvt(convert_fuel_efficiency)
            env.filters["cvt_fuel_vol"] = _cvt(convert_fuel_volume)

    return app


app = create_app()
