"""Source adapter Protocol.

Ingestion-pipeline-facing adapters duck-type to this shape. Concrete adapters
live under `web/services/sources/<source_name>/adapter.py` (e.g.
`ha_fordpass/adapter.py` introduced in Plan 29-02).
"""

from typing import Any, Protocol, runtime_checkable

from web.services.units.contracts import FieldContract


@runtime_checkable
class SourceAdapter(Protocol):
    FIELD_CONTRACTS: list[FieldContract]

    async def process_event(self, entity_id: str, new_state: dict, db: Any) -> None:
        ...
