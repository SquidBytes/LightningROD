"""FordConnect adapter: maps `/telemetry` metrics onto LightningROD tables."""

from __future__ import annotations

from typing import Any

from web.services.units.data_map import DataMapping

DATA_MAP: list[DataMapping] = []


async def process_sample(
    source_ref: str,
    payload: dict,
    db: Any,
    source_config: dict | None = None,
) -> None:
    """Parse one endpoint response. Nothing is mapped yet."""
