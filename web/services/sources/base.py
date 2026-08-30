"""Source adapter Protocol.

Ingestion-pipeline-facing adapters duck-type to this shape. Concrete adapters
live under `web/services/sources/<source_name>/adapter.py`.

The Protocol is transport-neutral: a *source_ref* says where the sample came
from and a *payload* carries it. What those mean is the source's business —
a Home Assistant adapter reads the source_ref as an entity_id and the payload
as a `state_changed` new_state; a polling HTTP adapter reads them as an
endpoint name and its decoded response body. `SourceKind`
(`web/services/units/data_map.py`) names which reading applies.
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
