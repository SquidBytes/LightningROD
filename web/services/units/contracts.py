"""FieldContract — the observable unit contract primitive (D-C1).

Every source adapter owns a `FIELD_CONTRACTS: list[FieldContract]` registry
mapping ingested fields to their declared source entity, attribute, and unit,
plus the DB target. The registry is the single source of truth for:
- Adapter unit conversion (source_unit feeds to_metric)
- Auto-generated docs (scripts/gen_data_sources_doc.py, Plan 29-03)
- Runtime diagnostic endpoint (/admin/data-sources, Plan 29-03)
- Contract-coverage invariant test (tests/test_unit/test_contract_coverage.py)
- Future data-recovery phase lookup table (D-F3)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldContract:
    source_entity_pattern: str   # e.g. "sensor.fordpass_{vin}_metrics"
    source_attribute: str        # e.g. "xevBatteryRange"
    source_unit: str             # e.g. "km" — must be recognized by to_metric()
    target_db_table: str         # e.g. "ev_battery_status"
    target_db_column: str        # e.g. "hv_battery_range"
    target_unit: str             # canonical metric unit, e.g. "km"
    notes: str = ""              # human-readable provenance / caveats
