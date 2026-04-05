"""API test fixtures: async HTTP client with test DB session injected."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from web.dependencies import get_db


@pytest_asyncio.fixture
async def client(db_session):
    """Async HTTP client backed by the test DB session.

    Creates a fresh FastAPI app without the production lifespan (which would
    try to start HASS service and seed charger templates via the production
    engine). Instead, we build a minimal app with routes only and override
    the get_db dependency to inject our test session.
    """
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from web.routes import (
        battery, charging, costs, csv_import, dashboard,
        performance, review, sessions, settings, trips,
    )
    from web.main import localtime_filter

    app = FastAPI(title="LightningROD-Test")
    app.mount("/static", StaticFiles(directory="web/static"), name="static")
    app.include_router(dashboard.router)
    app.include_router(sessions.router, prefix="/charging")
    app.include_router(costs.router, prefix="/charging")
    app.include_router(performance.router, prefix="/charging")
    app.include_router(settings.router)
    app.include_router(csv_import.router)
    app.include_router(battery.router)
    app.include_router(charging.router)
    app.include_router(review.router)
    app.include_router(trips.router)

    # Register localtime filter on Jinja2 templates
    for route_module in [dashboard, sessions, costs, performance, settings,
                         csv_import, charging, review, battery, trips]:
        if hasattr(route_module, "templates"):
            route_module.templates.env.filters["localtime"] = localtime_filter

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
