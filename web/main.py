from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db.engine import AsyncSessionLocal, engine
from web.queries.settings import seed_charger_templates, seed_predefined_networks
from web.routes import csv_import, dashboard, sessions, costs, energy, settings


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
    return app


app = create_app()
