"""FordConnect adapter: maps endpoint responses onto LightningROD tables."""

from __future__ import annotations

import logging
from typing import Any

from web.services.pipeline.context import IngestContext
from web.services.pipeline.location import record_position
from web.services.pipeline.observation import Observation
from web.services.pipeline.status import upsert_status
from web.services.sources.fcon.charge_sessions import record_session_if_ended
from web.services.sources.fcon.garage import fill_battery, record_garage
from web.services.sources.fcon.identity import SOURCE_SYSTEM, device_id_of
from web.services.sources.fcon.trips import record_trip_if_ended
from web.services.sources.ford_metrics.last_seen import record_last_seen
from web.services.sources.ford_metrics.parser import data_map, parse_metrics
from web.services.units.data_map import DataMapping, SourceKind, SourceRef
from web.services.vehicles.auto_create import ensure_vehicle

logger = logging.getLogger("lightningrod.sources.fcon")

TELEMETRY = SourceRef("telemetry", SourceKind.HTTP_ENDPOINT)
DATA_MAP: list[DataMapping] = data_map(TELEMETRY)
_last_seen_raw: dict[str, dict[str, Any]] = {}


def parse_telemetry(payload: dict, ctx: IngestContext) -> list[Observation]:
    """Status-table observations in one /telemetry response."""
    device_id = device_id_of(payload)
    if device_id is None:
        return []
    rows = parse_metrics(payload.get("metrics") or {})
    record_last_seen(_last_seen_raw, DATA_MAP, rows)
    return [Observation(device_id, r.table, r.recorded_at, r.values, ctx.source_system) for r in rows]


async def process_sample(
    source_ref: str, payload: dict, db: Any, source_config: dict | None = None
) -> None:
    """Parse one endpoint response; `source_ref` is the endpoint name."""
    ctx = IngestContext.from_config(SOURCE_SYSTEM, source_config)
    device_id = device_id_of(payload)
    if source_ref == "garage" and device_id is not None:
        await record_garage(db, device_id, payload)
    if source_ref != "telemetry":
        return
    if device_id is None:
        logger.warning("fcon telemetry without a vin; skipped")
        return
    vehicle = await ensure_vehicle(db, device_id, source_system=SOURCE_SYSTEM, vin=payload.get("vin"))
    observations = parse_telemetry(payload, ctx)
    capacity = next((c for o in observations if (c := o.values.get("hv_battery_capacity"))), None)
    if vehicle is not None and capacity:
        fill_battery(vehicle, float(capacity))
    await upsert_status(db, observations)
    await record_position(db, device_id, payload.get("metrics") or {}, ctx.source_system)
    await record_session_if_ended(db, device_id, payload, ctx.config_id)
    await record_trip_if_ended(db, device_id, payload)
