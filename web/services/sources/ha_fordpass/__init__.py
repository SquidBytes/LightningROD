"""ha-fordpass source adapter package. See `adapter.py`."""

from web.services.sources.base import DataSource
from web.services.sources.ha_fordpass.config import HAFordpassConfig

DATA_SOURCE = DataSource(
    source_name="ha_fordpass",
    display_name="Home Assistant (FordPass)",
    adapter_module="web.services.sources.ha_fordpass.adapter",
    config_schema=HAFordpassConfig,
    setup_flow="ha_websocket",
    secret_fields=frozenset({"ha_token"}),
)
