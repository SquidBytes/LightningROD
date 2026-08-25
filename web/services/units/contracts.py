"""Unit-contract datatypes shared by source adapters and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceLocatorKind(StrEnum):
    """How a source locator's pattern string is interpreted by an adapter."""

    HA_ENTITY_ID = "ha_entity_id"
    # Reserved for future: HTTP_ENDPOINT = "http_endpoint", MQTT_TOPIC = "mqtt_topic"


@dataclass(frozen=True)
class SourceLocator:
    """Typed locator for a contract's source — pattern + how to interpret it."""

    pattern: str
    kind: SourceLocatorKind


@dataclass(frozen=True)
class FieldContract:
    """Mapping from one source attribute to one target DB column in metric units.

    `ha_unit_system_converted` flags fields that Home Assistant itself converts
    before emitting — i.e. fields where ha-fordpass calls `localize_distance` /
    `localize_temperature` / `units.pressure()` inside its `attrs_fn`. For
    those fields the effective source unit at read-time depends on
    `ha_config.unit_system` (imperial -> mi/°F, metric -> km/°C), and the
    adapter's `_resolve_source_unit` derives it per-event. Leave False for
    raw-API passthrough fields (metrics/events entities, raw attributes on
    energytransferlogentry, etc.).
    """

    source_locator: SourceLocator
    source_attribute: str
    source_unit: str
    target_db_table: str
    target_db_column: str
    target_unit: str
    notes: str | None = None
    ha_unit_system_converted: bool = False
    # True for fields read from an entity's own state rather than one of its
    # attributes. Home Assistant publishes those already converted into the
    # viewer's display unit and stamps that unit on the event, so the adapter
    # resolves the source unit from `attributes.unit_of_measurement` per event.
    read_time_uom: bool = False
    # ha-fordpass versions disagree on capacity scale (raw Wh vs already-kWh);
    # when True and source_unit is Wh, |value| < 1000 is treated as kWh.
    wh_autoscale: bool = False
