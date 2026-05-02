"""ha-gas-price adapter.

Matches incoming HA state_changed events against configured gas-price
sensor entity_ids. v1 reads entity_ids from app_settings keys
(gas_sensor_station_entity_id, gas_sensor_average_entity_id). A future
milestone migrates these into data_source_configs[ha_gas_price].config_json.

This adapter has no FIELD_CONTRACTS today: gas-price values are unitless
(currency per gallon) and handled by direct write helpers in
`web/queries/gas_prices.py`. The empty FIELD_CONTRACTS list is required
by the SourceAdapter Protocol (`web/services/sources/base.py`) and by
the admin diagnostic page (`web/routes/admin/data_sources.py`).

The runtime's per-event fan-out calls `try_handle_event` first; on False
return, the runtime falls through to slug-based ha_fordpass dispatch. The
fan-out collapses the legacy `process_state_change` gas-sensor branch
(read entity_ids, then dispatch to the gas writer) into one entry point.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from web.services.units.contracts import FieldContract

logger = logging.getLogger("lightningrod.sources.ha_gas_price")

# Required by SourceAdapter Protocol; gas-price has no unit-ful columns.
FIELD_CONTRACTS: list[FieldContract] = []

# Cache for gas sensor entity_ids from app_settings to avoid per-event DB query
_gas_sensor_cache: dict[str, str] = {}
_gas_sensor_cache_ts: float = 0.0
_GAS_SENSOR_CACHE_TTL = 300  # seconds (5 minutes)


async def _get_gas_sensor_entity_ids(db) -> tuple[str | None, str | None]:
    """Return (station_entity_id, average_entity_id) from app_settings, cached.

    Cache is refreshed every 5 minutes to pick up configuration changes
    without querying app_settings on every single event.
    """
    global _gas_sensor_cache, _gas_sensor_cache_ts

    now = time.time()
    if _gas_sensor_cache and (now - _gas_sensor_cache_ts) < _GAS_SENSOR_CACHE_TTL:
        logger.debug("Gas sensor cache hit")
        return (
            _gas_sensor_cache.get("gas_sensor_station_entity_id"),
            _gas_sensor_cache.get("gas_sensor_average_entity_id"),
        )

    from web.queries.settings import get_app_settings_dict

    gas_settings = await get_app_settings_dict(
        db, ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"]
    )
    _gas_sensor_cache = gas_settings
    _gas_sensor_cache_ts = now
    logger.debug("Gas sensor cache refreshed: %s", gas_settings)
    return (
        gas_settings.get("gas_sensor_station_entity_id") or None,
        gas_settings.get("gas_sensor_average_entity_id") or None,
    )


def invalidate_gas_sensor_cache() -> None:
    """Invalidate the gas sensor entity_id cache.

    Call this when gas sensor settings are updated via the settings UI.
    """
    global _gas_sensor_cache, _gas_sensor_cache_ts
    _gas_sensor_cache = {}
    _gas_sensor_cache_ts = 0.0


async def try_handle_event(entity_id: str, new_state: dict, db) -> bool:
    """Per-event entry point used by the HA WebSocket runtime's dispatch.

    Returns True if entity_id matched a configured gas sensor and the event
    was processed (or skipped as non-numeric). Returns False otherwise —
    the runtime falls through to slug-based ha_fordpass dispatch.

    Reads the configured entity_ids (cached) and delegates to
    `handle_gas_sensor_event`. Short-circuits to False when neither
    entity_id is configured.
    """
    station_entity, average_entity = await _get_gas_sensor_entity_ids(db)
    if not (station_entity or average_entity):
        return False
    if entity_id != station_entity and entity_id != average_entity:
        return False
    from web.services.sources.ha_gas_price.handlers import handle_gas_sensor_event

    return await handle_gas_sensor_event(
        entity_id, new_state, station_entity, average_entity, db
    )


async def process_event(entity_id: str, new_state: dict, db: Any) -> None:
    """SourceAdapter Protocol entry point. Delegates to try_handle_event.

    The Protocol requires `process_event` returning None; gas-price's
    natural entry point is `try_handle_event` (returns bool). This shim
    adapts that signature for any future caller that walks REGISTRY and
    dispatches by Protocol shape.
    """
    await try_handle_event(entity_id, new_state, db)
