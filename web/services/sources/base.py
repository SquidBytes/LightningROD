"""Base types every source package builds on.

Ingestion-pipeline-facing adapters duck-type to the `SourceAdapter` shape.
Concrete adapters live under `web/services/sources/<source_name>/adapter.py`,
and each package declares its own `DATA_SOURCE`.

Transport-neutral by design: what `source_ref` and `payload` mean is the
source's business — an entity_id and a `state_changed` new_state for a Home
Assistant adapter, an endpoint name and a response body for a polling HTTP
one. `SourceKind` names which reading applies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from web.services.units.data_map import DataMapping

if TYPE_CHECKING:
    from db.models.data_source_config import DataSourceConfig
    from web.services.ingestion.base import IngestionRuntime

# (config row, decrypted config_json) -> runtime. The row's own config_json
# still holds ciphertext; builders read the second argument.
RuntimeBuilder = Callable[["DataSourceConfig", dict], "IngestionRuntime"]


@runtime_checkable
class SourceAdapter(Protocol):
    DATA_MAP: list[DataMapping]

    async def process_sample(
        self,
        source_ref: str,
        payload: dict,
        db: Any,
        source_config: dict | None = None,
    ) -> None:
        ...


@dataclass(frozen=True)
class DataSource:
    """One shipped source: its config schema, adapter module and setup flow.

    `secret_fields` are the config_json keys encrypted at rest. `build_runtime`
    is None for a source with no transport of its own.
    """

    source_name: str
    display_name: str
    adapter_module: str
    config_schema: type[BaseModel]
    setup_flow: Literal["ha_websocket", "oauth", "static", "none"]
    secret_fields: frozenset[str] = frozenset()
    build_runtime: RuntimeBuilder | None = None
