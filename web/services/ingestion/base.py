"""Ingestion runtime Protocol.

Each connection-shaped runtime (HA WebSocket, FCON polling, MQTT, ha-bluelink)
implements this Protocol. Concrete runtimes live under
`web/services/ingestion/<transport>.py`. The supervisor
(`web/services/ingestion/supervisor.py`) spawns one runtime instance per
enabled `data_source_configs` row.

Shape mirrors `web/services/sources/base.py:SourceAdapter` exactly.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IngestionRuntime(Protocol):
    config_id: int

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    @property
    def health(self) -> dict[str, Any]: ...
