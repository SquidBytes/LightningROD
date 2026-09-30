"""FordConnect adapter: maps endpoint responses onto LightningROD tables."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from web.services.sources.fcon.charge_sessions import record_session_if_ended
from web.services.sources.fcon.garage import fill_battery, garage_facts
from web.services.sources.fcon.identity import SOURCE_SYSTEM, device_id_of
from web.services.sources.fcon.positions import record_position
from web.services.sources.fcon.trips import record_trip_if_ended
from web.services.sources.ford_metrics.parser import data_map, parse_metrics
from web.services.sources.ford_metrics.writer import upsert_rows
from web.services.units.data_map import DataMapping, SourceKind, SourceRef
from web.services.vehicles.auto_create import ensure_vehicle

logger = logging.getLogger("lightningrod.sources.fcon")

TELEMETRY = SourceRef("telemetry", SourceKind.HTTP_ENDPOINT)
DATA_MAP: list[DataMapping] = data_map(TELEMETRY)
_BY_FIELD = {m.source_field: m for m in DATA_MAP}

# Keyed by DataMapping.key, read back by /admin/data-sources.
_last_seen_raw: dict[str, dict[str, Any]] = {}


def _record_last_seen(metric: str, entry: Any, converted: Any) -> None:
    mapping = _BY_FIELD.get(metric)
    if mapping is None:
        return
    value = entry.get("value") if isinstance(entry, dict) else entry
    _last_seen_raw[mapping.key] = {
        "value": value,
        "unit": mapping.source_unit,
        "declared_unit": mapping.source_unit,
        "method": "declared",
        "seen_at": datetime.now(UTC).isoformat(),
        "converted": converted,
    }


async def process_sample(
    source_ref: str,
    payload: dict,
    db: Any,
    source_config: dict | None = None,
) -> None:
    """Parse one endpoint response; `source_ref` is the endpoint name."""
    device_id = device_id_of(payload)
    if source_ref == "garage":
        if device_id:
            garage = garage_facts(payload)
            await ensure_vehicle(
                db,
                device_id,
                source_system=SOURCE_SYSTEM,
                vin=payload.get("vin"),
                display_name=garage.display_name,
                auto_names=garage.auto_names,
                **garage.facts,
            )
        return
    if source_ref != "telemetry":
        return
    if device_id is None:
        logger.warning("fcon telemetry without a vin; skipped")
        return
    vehicle = await ensure_vehicle(
        db, device_id, source_system=SOURCE_SYSTEM, vin=payload.get("vin")
    )
    rows = parse_metrics(payload.get("metrics") or {})
    capacity = next(
        (r.values.get("hv_battery_capacity") for r in rows if "hv_battery_capacity" in r.values),
        None,
    )
    if vehicle is not None and capacity:
        fill_battery(vehicle, float(capacity))
    for row in rows:
        for metric, entry in row.raw.items():
            column = _BY_FIELD[metric].db_column
            _record_last_seen(metric, entry, row.values.get(column))
    await upsert_rows(db, device_id, rows, source_system=SOURCE_SYSTEM)
    await record_position(db, device_id, payload.get("metrics") or {})
    config_id = (source_config or {}).get("config_id")
    await record_session_if_ended(db, device_id, payload, config_id)
    await record_trip_if_ended(db, device_id, payload)
