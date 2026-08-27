"""Source adapter Protocol.

Ingestion-pipeline-facing adapters duck-type to this shape. Concrete adapters
live under `web/services/sources/<source_name>/adapter.py`.

The contract is transport-neutral: a *locator* says where the sample came
from and a *payload* carries it. What those mean is the source's business —
a Home Assistant adapter reads the locator as an entity_id and the payload as
a `state_changed` new_state; a polling HTTP adapter reads them as an endpoint
name and its decoded response body. `SourceLocatorKind`
(`web/services/units/contracts.py`) names which reading applies.
"""

from typing import Any, Protocol, runtime_checkable

from web.services.units.contracts import FieldContract


@runtime_checkable
class SourceAdapter(Protocol):
    FIELD_CONTRACTS: list[FieldContract]

    async def process_sample(
        self,
        locator: str,
        payload: dict,
        db: Any,
        source_config: dict | None = None,
    ) -> None:
        ...
