"""Source-adapter registry.

Single source of truth for which source adapters this app ships. The admin
diagnostic and the auto-doc generator both iterate this list instead of
maintaining their own hardcoded module manifests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from web.services.sources.ha_fordpass.config import HAFordpassConfig
from web.services.sources.ha_gas_price.config import HAGasPriceConfig


@dataclass(frozen=True)
class DataSourceDescriptor:
    source_name: str
    display_name: str
    adapter_module: str
    config_schema: type[BaseModel]
    setup_flow: Literal["ha_websocket", "oauth", "static", "none"]


REGISTRY: list[DataSourceDescriptor] = [
    DataSourceDescriptor(
        source_name="ha_fordpass",
        display_name="Home Assistant (FordPass)",
        adapter_module="web.services.sources.ha_fordpass.adapter",
        config_schema=HAFordpassConfig,
        setup_flow="ha_websocket",
    ),
    DataSourceDescriptor(
        source_name="ha_gas_price",
        display_name="Home Assistant (Gas Price Sensors)",
        adapter_module="web.services.sources.ha_gas_price.adapter",
        config_schema=HAGasPriceConfig,
        setup_flow="static",
    ),
]
