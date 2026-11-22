"""Store totals as integer cents, rates as NUMERIC(12,4), measurements as double precision.

Revision ID: money_storage
Revises: drop_ev_statistics
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "money_storage"
down_revision = "drop_ev_statistics"
branch_labels = None
depends_on = None

CENTS = {
    "ev_charging_session": ["cost", "cost_without_overrides", "estimated_cost"],
    "ev_network_subscriptions": ["monthly_fee"],
}
RATES = {
    "ev_charging_networks": ["cost_per_kwh"],
    "ev_location_lookup": ["cost_per_kwh"],
    "ev_network_subscriptions": ["member_rate"],
    "gas_price_history": ["station_price", "average_price"],
    "gas_price_readings": ["price"],
}
MEASUREMENTS = {
    "ev_battery_status": [
        "hv_battery_soc", "hv_battery_actual_soc", "hv_battery_voltage", "hv_battery_amperage",
        "hv_battery_kw", "hv_battery_capacity", "hv_battery_range", "hv_battery_max_range",
        "hv_battery_temperature", "lv_battery_level", "lv_battery_voltage", "motor_voltage",
        "motor_amperage", "motor_kw",
    ],
    "ev_charger_stalls": ["rated_kw", "voltage", "amperage"],
    "ev_charging_session": [
        "charging_voltage", "charging_amperage", "charging_kw", "charge_duration_seconds",
        "plugged_in_duration_seconds", "start_soc", "end_soc", "energy_kwh", "latitude",
        "longitude", "max_power", "min_power", "distance_added", "evse_voltage", "evse_amperage",
        "evse_kw", "evse_energy_kwh", "evse_max_power_kw", "charger_rated_kw",
        "battery_temp_start", "battery_temp_end", "ambient_temp_start", "ambient_temp_end",
    ],
    "ev_location": ["latitude", "longitude", "gps_accuracy", "altitude"],
    "ev_location_gps_aliases": ["latitude", "longitude"],
    "ev_location_lookup": ["latitude", "longitude"],
    "ev_trip_metrics": [
        "distance", "duration", "odometer_start", "odometer_end", "energy_consumed",
        "efficiency", "range_regenerated", "ambient_temp", "cabin_temp", "outside_air_temp",
        "driving_score", "speed_score", "acceleration_score", "deceleration_score",
        "electrical_efficiency", "brake_torque",
    ],
    "ev_vehicle_status": [
        "odometer", "speed", "accelerator_position", "torque_at_transmission", "brake_torque",
        "yaw_rate", "acceleration", "outside_temperature", "cabin_temperature",
    ],
    "ev_vehicles": ["battery_capacity_kwh", "battery_gross_capacity_kwh"],
    "ice_vehicles": ["fuel_efficiency_l_per_100km", "tank_capacity_l"],
}
TABLES = sorted({*CENTS, *RATES, *MEASUREMENTS})


# Inner round absorbs float noise so 3.675 stored as 3.67499999... still rounds up.
_TO_CENTS = "ROUND(ROUND({c} * 100, 8))"
_KINDS = (("cents", CENTS), ("rate", RATES), ("measurement", MEASUREMENTS))
_SA_TYPES: dict[str, sa.types.TypeEngine] = {
    "cents": sa.BigInteger(),
    "rate": sa.Numeric(12, 4),
    "measurement": sa.Double(),
}
_PG_UPGRADE = {
    "cents": f"BIGINT USING {_TO_CENTS}",
    "rate": "NUMERIC(12, 4)",
    "measurement": "DOUBLE PRECISION",
}
_PG_DOWNGRADE = {"cents": "NUMERIC USING {c} / 100.0", "rate": "NUMERIC", "measurement": "NUMERIC"}


def _columns(table: str) -> list[tuple[str, str]]:
    return [(column, kind) for kind, tables in _KINDS for column in tables.get(table, [])]


def _is_postgresql() -> bool:
    # The context knows the dialect in --sql mode too, where there is no bind.
    return op.get_context().dialect.name == "postgresql"


def _pg_retype(table: str, type_sql: dict[str, str]) -> None:
    """One ALTER TABLE per table, so PostgreSQL rewrites it once."""
    clauses = [
        f"ALTER COLUMN {column} TYPE {type_sql[kind].format(c=column)}"
        for column, kind in _columns(table)
    ]
    op.execute(f"ALTER TABLE {table} {', '.join(clauses)}")


def _batch_retype(table: str, *, to_numeric: bool) -> None:
    """Batch mode copies the table once, which SQLite needs to alter a type."""
    # SQLite commits the copy table's CREATE on its own, so a failed run leaves it behind.
    op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{table}")
    with op.batch_alter_table(table) as batch_op:
        for column, kind in _columns(table):
            new, old = _SA_TYPES[kind], sa.Numeric()
            batch_op.alter_column(
                column,
                type_=old if to_numeric else new,
                existing_type=new if to_numeric else old,
            )


def upgrade() -> None:
    for table in TABLES:
        if _is_postgresql():
            _pg_retype(table, _PG_UPGRADE)
            continue
        for column in CENTS.get(table, []):
            op.execute(f"UPDATE {table} SET {column} = {_TO_CENTS.format(c=column)}")
        for column in RATES.get(table, []):
            op.execute(f"UPDATE {table} SET {column} = ROUND({column}, 4)")
        _batch_retype(table, to_numeric=False)


def downgrade() -> None:
    for table in TABLES:
        if _is_postgresql():
            _pg_retype(table, _PG_DOWNGRADE)
            continue
        _batch_retype(table, to_numeric=True)
        for column in CENTS.get(table, []):
            op.execute(f"UPDATE {table} SET {column} = {column} / 100.0")
