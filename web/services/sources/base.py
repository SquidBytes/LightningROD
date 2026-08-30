"""Source adapter Protocol.

Ingestion-pipeline-facing adapters duck-type to this shape. Concrete adapters
live under `web/services/sources/<source_name>/adapter.py`.

Transport-neutral by design: what `source_ref` and `payload` mean is the
source's business — an entity_id and a `state_changed` new_state for a Home
Assistant adapter, an endpoint name and a response body for a polling HTTP
one. `SourceKind` names which reading applies.
"""

from typing import Any, Protocol, runtime_checkable

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
