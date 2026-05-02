"""ha-gas-price event handler.

Writes a GasPriceReading + refreshes monthly averages in GasPriceHistory.
The leading underscore on the legacy private name is dropped on relocation
because the function is now the public entry the adapter exposes; the
underscore was a hass_processor-module convention.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from web.services.ingestion._helpers import (
    _get_event_timestamp,
    _get_state_value,
    _safe_float,
)

logger = logging.getLogger("lightningrod.sources.ha_gas_price.handlers")


async def handle_gas_sensor_event(
    entity_id: str,
    new_state: dict,
    station_entity: str | None,
    average_entity: str | None,
    db,
) -> bool:
    """Handle a gas price sensor event if entity_id matches configured sensors.

    Returns True if the event was handled (entity_id matched), False otherwise.
    Non-numeric state values are skipped gracefully.
    """
    if entity_id != station_entity and entity_id != average_entity:
        return False

    state_val = _get_state_value(new_state)
    if state_val is None or state_val in ("unknown", "unavailable", ""):
        logger.debug("Gas sensor %s has non-numeric state '%s', skipping", entity_id, state_val)
        return True  # Matched but not actionable

    price = _safe_float(state_val)
    if price is None or price <= 0:
        logger.debug("Gas sensor %s value not a valid price: '%s', skipping", entity_id, state_val)
        return True

    recorded_at = _get_event_timestamp(new_state) or datetime.now(UTC)

    from web.queries.gas_prices import (
        compute_monthly_averages,
        store_gas_price_reading,
        upsert_gas_price,
    )

    await store_gas_price_reading(db, entity_id, price, recorded_at)
    logger.info("Gas price reading stored: entity=%s, price=%.3f, at=%s", entity_id, price, recorded_at)

    # Compute monthly averages and upsert into gas_price_history
    month_avg = await compute_monthly_averages(db, entity_id)
    for (year, month), avg_price in month_avg.items():
        if entity_id == station_entity:
            await upsert_gas_price(db, year, month, station_price=avg_price, source="ha_sensor")
        elif entity_id == average_entity:
            await upsert_gas_price(db, year, month, average_price=avg_price, source="ha_sensor")

    return True
