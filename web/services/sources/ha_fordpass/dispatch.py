"""ha-fordpass slug-based event dispatcher.

`@handles` decorator + `SENSOR_HANDLERS` registry + `dispatch_slug`
entrypoint. Slug handlers themselves live in `handlers.py` and self-register
via the decorator at import time.

The gas-price branch lives in the `ha_gas_price` source adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger("lightningrod.sources.ha_fordpass.dispatch")

SENSOR_HANDLERS: dict[str, Callable] = {}


def handles(*slugs):
    """Register a handler for one or more sensor slugs.

    Each decorated coroutine is appended to `SENSOR_HANDLERS` keyed by every
    slug listed; the decorator runs at import time so importing the
    `handlers` module is sufficient to populate the registry.
    """
    def decorator(fn):
        for slug in slugs:
            SENSOR_HANDLERS[slug] = fn
        return fn
    return decorator


async def dispatch_slug(
    entity_id: str,
    new_state: dict,
    ha_config: dict,
    db,
    *,
    config_id: int,
) -> None:
    """Fan out a state_changed event to the slug-keyed handler.

    `config_id` is propagated so handler-side pending-state dicts can rekey
    on `(config_id, device_id)` when N>1 ha_fordpass runtimes observe the
    same VIN. The handler signatures stay 5-arg; `config_id` is smuggled
    into `ha_config` under the `_config_id` key (leading underscore signals
    supervisor-managed metadata, not HA-API).

    The session lifecycle (open/commit/rollback) is owned by the caller —
    `dispatch_slug` itself does not commit or rollback.
    """
    # Import handlers module here to trigger the @handles decorator
    # side-effects that populate SENSOR_HANDLERS. Top-level import would
    # create a cycle (handlers.py imports `handles` from this module).
    from web.services.sources.ha_fordpass import handlers  # noqa: F401
    from web.services.sources.ha_fordpass.handlers import (
        _ensure_vehicle_exists,
        extract_slug,
        get_device_id,
    )

    slug = extract_slug(entity_id)
    if slug is None or slug not in SENSOR_HANDLERS:
        return

    handler = SENSOR_HANDLERS[slug]
    device_id = get_device_id(entity_id, ha_config)

    await _ensure_vehicle_exists(device_id, entity_id, db)
    # Smuggle config_id through ha_config so handler signatures stay 5-arg.
    ha_config_with_cid = {**ha_config, "_config_id": config_id}
    await handler(slug, new_state, ha_config_with_cid, device_id, db)
