"""What a source parser hands to the shared writers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from db.models.base import FCON_INGEST_SCHEMA_VERSION, HA_INGEST_SCHEMA_VERSION
from web.services.source_system import SourceSystem

INGEST_SCHEMA_VERSION: dict[str, int] = {
    SourceSystem.FCON: FCON_INGEST_SCHEMA_VERSION,
    SourceSystem.HA_FORDPASS: HA_INGEST_SCHEMA_VERSION,
}


@dataclass(frozen=True)
class Observation:
    """Columns one source read for one status-table row at one instant."""

    device_id: str
    table: str
    recorded_at: datetime
    values: dict[str, Any]
    source_system: str


@dataclass(frozen=True)
class TripObservation:
    """One finished drive; `values` holds trip columns including `end_time`."""

    device_id: str
    trip_id: uuid.UUID
    values: dict[str, Any]
    source_system: str


@dataclass(frozen=True)
class SessionObservation:
    """One finished charge; `values` holds session columns including `session_start_utc`."""

    device_id: str
    recorded_at: datetime
    values: dict[str, Any]
    source_system: str
    # The source's own name for the place; used only when no location resolves.
    location_name: str | None = None
