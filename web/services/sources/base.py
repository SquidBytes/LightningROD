"""Base types every source package builds on.

Ingestion-pipeline-facing adapters duck-type to the `SourceAdapter` shape.
Concrete adapters live under `web/services/sources/<source_name>/adapter.py`,
and each package declares its own `DATA_SOURCE`.

Transport-neutral by design: what `source_ref` and `payload` mean is the
source's business — an entity_id and a `state_changed` new_state for a Home
Assistant adapter, an endpoint name and a response body for a polling HTTP
one. `SourceKind` names which reading applies.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from web.services.units.data_map import DataMapping


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

    `secret_fields` is a declaration only — nothing encrypts or redacts on it
    yet; it gives the config-row boundary one place to ask what is sensitive.
    """

    source_name: str
    display_name: str
    adapter_module: str
    config_schema: type[BaseModel]
    setup_flow: Literal["ha_websocket", "oauth", "static", "none"]
    secret_fields: frozenset[str] = frozenset()
