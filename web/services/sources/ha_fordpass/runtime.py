"""Runtime builder for the ha_fordpass source.

The supervisor calls `build_runtime(row, config)` and stays free of per-source
construction knowledge: which transport this source speaks, and which
`config_json` keys feed it, are decided here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.models.data_source_config import DataSourceConfig
from web.services.sources.ha_fordpass.config import HAFordpassConfig

if TYPE_CHECKING:
    from web.services.ingestion.base import IngestionRuntime


def build_runtime(row: DataSourceConfig, config: dict) -> IngestionRuntime:
    """Build the HA WebSocket runtime for one enabled ha_fordpass config row.

    `config` is the row's decrypted config_json. Raises on an invalid config;
    the supervisor logs and skips the row, so raise nothing that embeds
    config values.
    """
    # Deferred: `web.services.ingestion` imports the supervisor, which imports
    # this module. Importing the transport at module scope would close the loop.
    from web.services.ingestion.ha_websocket import HAWebSocketRuntime

    cfg = HAFordpassConfig.model_validate(config)
    return HAWebSocketRuntime(
        config_id=row.id,
        ha_url=cfg.ha_url,
        ha_token=cfg.ha_token,
        source_name=row.source_name,
        instance_label=row.instance_label,
    )
