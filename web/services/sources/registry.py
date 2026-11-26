"""Source registry.

Single source of truth for which sources this app ships. The admin diagnostic
and the auto-doc generator iterate this list instead of maintaining their own
module manifests. Each source declares itself in its own package.
"""

from __future__ import annotations

from web.services.sources.base import DataSource
from web.services.sources.ford import fcon, ha_fordpass
from web.services.sources.home_assistant import ha_gas_price

REGISTRY: list[DataSource] = [
    ha_fordpass.DATA_SOURCE,
    ha_gas_price.DATA_SOURCE,
    fcon.DATA_SOURCE,
]
