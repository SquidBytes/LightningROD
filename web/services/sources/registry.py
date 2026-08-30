"""Source registry.

Single source of truth for which sources this app ships. The admin diagnostic
and the auto-doc generator iterate this list instead of maintaining their own
module manifests. Each source declares itself in its own package.
"""

from __future__ import annotations

from web.services.sources import ha_fordpass, ha_gas_price
from web.services.sources.base import DataSource

REGISTRY: list[DataSource] = [
    ha_fordpass.DATA_SOURCE,
    ha_gas_price.DATA_SOURCE,
]
