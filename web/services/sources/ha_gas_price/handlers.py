"""ha-gas-price event handler.

Writes a GasPriceReading + refreshes monthly averages in GasPriceHistory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from web.services.ingestion._helpers import (
    _get_event_timestamp,
    _get_state_value,
)
from web.unit_system import to_metric_price_per_volume

logger = logging.getLogger("lightningrod.sources.ha_gas_price.handlers")


def metric_gas_price(state_val: Any) -> Decimal | None:
    """HA $/gal state text as Decimal $/L; None unless it is a positive number."""
    try:
        price = Decimal(str(state_val))
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0:
        return None
    # HA gas sensors report $/gal; all gas-price storage is metric.
    return to_metric_price_per_volume(price, "us")


async def handle_gas_sensor_event(
    entity_id: str,
    new_state: dict,
    station_entity: str | None,
    average_entity: str | None,
    db,
) -> bool:
    """Handle a gas price sensor event if entity_id matches configured sensors.

    Returns True only when a row is written (or staged for write on the
    caller's session). Returns False when the event is not actionable —
    either the entity_id does not match a configured sensor, the state is
    non-numeric, or the parsed price is non-positive. Returning False on
    matched-but-unactionable lets the dispatcher skip an empty commit and
    fall through to slug dispatch, which is a safe no-op for gas-sensor
    entity_ids (extract_slug returns None for non-fordpass entities).
    """
    if entity_id != station_entity and entity_id != average_entity:
        return False

    state_val = _get_state_value(new_state)
    if state_val is None or state_val in ("unknown", "unavailable", ""):
        logger.debug("Gas sensor %s has non-numeric state '%s', skipping", entity_id, state_val)
        return False

    price_metric = metric_gas_price(state_val)
    if price_metric is None:
        logger.debug("Gas sensor %s value not a valid price: '%s', skipping", entity_id, state_val)
        return False

    recorded_at = _get_event_timestamp(new_state) or datetime.now(UTC)

    from web.queries.gas_prices import (
        compute_monthly_averages,
        store_gas_price_reading,
        upsert_gas_price,
    )

    await store_gas_price_reading(db, entity_id, price_metric, recorded_at)
    logger.info(
        "Gas price reading stored: entity=%s, price=%.4f $/L (raw=%s $/gal), at=%s",
        entity_id, price_metric, state_val, recorded_at,
    )

    # Compute monthly averages and upsert into gas_price_history
    month_avg = await compute_monthly_averages(db, entity_id)
    for (year, month), avg_price in month_avg.items():
        if entity_id == station_entity:
            await upsert_gas_price(db, year, month, station_price=avg_price, source="ha_sensor")
        elif entity_id == average_entity:
            await upsert_gas_price(db, year, month, average_price=avg_price, source="ha_sensor")

    return True
