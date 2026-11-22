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


def _retype(table: str, new_types: dict[str, sa.types.TypeEngine], *, to_numeric: bool) -> None:
    """Change column types through batch mode, which SQLite needs to alter a type."""
    with op.batch_alter_table(table) as batch_op:
        for column, new_type in new_types.items():
            batch_op.alter_column(
                column,
                type_=sa.Numeric() if to_numeric else new_type,
                existing_type=new_type if to_numeric else sa.Numeric(),
            )


def _new_types(table: str) -> dict[str, sa.types.TypeEngine]:
    return {
        **{c: sa.BigInteger() for c in CENTS.get(table, [])},
        **{c: sa.Numeric(12, 4) for c in RATES.get(table, [])},
        **{c: sa.Double() for c in MEASUREMENTS.get(table, [])},
    }


def upgrade() -> None:
    for table in TABLES:
        # Inner round absorbs float noise so 3.675 stored as 3.67499999... still rounds up.
        for column in CENTS.get(table, []):
            op.execute(f"UPDATE {table} SET {column} = ROUND(ROUND({column} * 100, 6))")
        for column in RATES.get(table, []):
            op.execute(f"UPDATE {table} SET {column} = ROUND({column}, 4)")
        _retype(table, _new_types(table), to_numeric=False)


def downgrade() -> None:
    for table in TABLES:
        _retype(table, _new_types(table), to_numeric=True)
        for column in CENTS.get(table, []):
            op.execute(f"UPDATE {table} SET {column} = {column} / 100.0")
