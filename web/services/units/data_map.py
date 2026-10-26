"""Data-map datatypes shared by source adapters and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceKind(StrEnum):
    """How a SourceRef's pattern string is interpreted by an adapter."""

    # Value stays "ha_entity_id" — it may already be persisted.
    HA_ENTITY = "ha_entity_id"
    HTTP_ENDPOINT = "http_endpoint"
    # Reserved for future: MQTT_TOPIC = "mqtt_topic"


@dataclass(frozen=True)
class SourceRef:
    """Where a mapping reads from — pattern plus how to interpret it."""

    pattern: str
    kind: SourceKind


@dataclass(frozen=True)
class DataMapping:
    """Mapping from one source field to one DB column in metric units."""

    source: SourceRef
    source_field: str
    source_unit: str
    db_table: str
    db_column: str
    db_unit: str
    notes: str | None = None
    # Home Assistant converts these fields before emitting them, so the
    # effective source unit is the user's HA unit system, not `source_unit`.
    ha_unit_system_converted: bool = False
    # Read from the entity's own state, which HA has already converted into
    # the display unit it stamps on the event — resolve the unit per event.
    read_time_uom: bool = False
    # ha-fordpass versions disagree on capacity scale (raw Wh vs already-kWh);
    # when True and source_unit is Wh, |value| < 1000 is treated as kWh.
    wh_autoscale: bool = False

    @property
    def key(self) -> str:
        """Cache identity; kind keeps an endpoint and a same-named entity apart."""
        return f"{self.source.kind}|{self.source.pattern}|{self.source_field}"
