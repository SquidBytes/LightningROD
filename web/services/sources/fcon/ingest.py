"""Where polled FordConnect responses go: archive first, then parse."""

from __future__ import annotations

import logging
from typing import Any

from db.engine import AsyncSessionLocal
from web.services.sources.fcon import adapter
from web.services.sources.fcon.raw_store import fcon_raw_archive, utc_now

logger = logging.getLogger("lightningrod.sources.fcon.ingest")


async def store_sample(config_id: int, endpoint: str, payload: Any) -> None:
    """Archive one response; parse it only if it is new.

    Separate commits: a parse failure must not lose the raw copy it would be
    re-parsed from.
    """
    if not isinstance(payload, dict):
        return
    async with AsyncSessionLocal() as db:
        is_new = await fcon_raw_archive.store(db, config_id, endpoint, payload, utc_now())
        await db.commit()
    if is_new:
        async with AsyncSessionLocal() as db:
            await adapter.process_sample(endpoint, payload, db, {"config_id": config_id})
            await db.commit()
    try:
        await fcon_raw_archive.prune()
    except Exception:
        logger.exception("fcon raw archive prune failed")
