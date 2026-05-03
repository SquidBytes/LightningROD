"""Ingestion runtime layer.

Houses transport-shaped runtimes (HA WebSocket today; FCON polling, MQTT
tomorrow) plus the supervisor that spawns one per enabled
data_source_configs row. Source-shaped logic (slug handlers, gas-price
matchers) lives in `web/services/sources/<source_name>/`.

The supervisor singleton is exported here so callers import as
`from web.services.ingestion import supervisor`.
"""

from web.services.ingestion.supervisor import supervisor

__all__ = ["supervisor"]
