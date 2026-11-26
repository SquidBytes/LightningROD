"""ha-fordpass source adapter package. See `adapter.py`."""

from web.services.source_system import SourceSystem
from web.services.sources.base import DataSource
from web.services.sources.ford.ha_fordpass.config import HAFordpassConfig
from web.services.sources.ford.ha_fordpass.runtime import build_runtime

DATA_SOURCE = DataSource(
    source_name=SourceSystem.HA_FORDPASS,
    display_name="Home Assistant (FordPass)",
    adapter_module="web.services.sources.ford.ha_fordpass.adapter",
    config_schema=HAFordpassConfig,
    setup_flow="ha_websocket",
    secret_fields=frozenset({"ha_token"}),
    build_runtime=build_runtime,
)
