"""Where polled FordConnect responses go: one session per response."""

from __future__ import annotations

from typing import Any

from db.engine import AsyncSessionLocal
from web.services.sources.fcon import adapter


async def store_sample(config_id: int, endpoint: str, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    async with AsyncSessionLocal() as db:
        await adapter.process_sample(endpoint, payload, db, {"config_id": config_id})
        await db.commit()
