"""Phase v0.3: Metric-canonical DB storage with unit system settings

Renames imperial-named columns to unit-agnostic names and converts existing
data from imperial units to metric for consistent storage:

    ev_charging_session.miles_added       -> distance_added (km)
    ev_vehicles.ice_mpg                   -> ice_fuel_efficiency (L/100km)
    ev_vehicles.ice_fuel_tank_gal         -> ice_fuel_tank_capacity (liters)
    ev_statistics.total_miles_added       -> total_distance_added (km)
    ev_statistics.avg_miles_per_kwh       -> avg_efficiency (km/kWh)

Also migrates the old `efficiency_unit` app setting to two independent
settings: `distance_unit` and `temp_unit`.

NOT converted: ev_trip_metrics.efficiency — historically stored raw from
FordPass without unit normalization, so the source unit of existing rows
is ambiguous. Fixed going forward in the ingestion layer.

Revision ID: s30_metric_canonical_storage
Revises: r23_alias_session_review
Create Date: 2026-04-04
"""

from alembic import op

revision = "s30_metric_canonical_storage"
down_revision = "r23_alias_session_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Rename columns ------------------------------------------------------
    op.alter_column(
        "ev_charging_session", "miles_added", new_column_name="distance_added"
    )
    op.alter_column("ev_vehicles", "ice_mpg", new_column_name="ice_fuel_efficiency")
    op.alter_column(
        "ev_vehicles", "ice_fuel_tank_gal", new_column_name="ice_fuel_tank_capacity"
    )
    op.alter_column(
        "ev_statistics", "total_miles_added", new_column_name="total_distance_added"
    )
    op.alter_column(
        "ev_statistics", "avg_miles_per_kwh", new_column_name="avg_efficiency"
    )

    # --- Convert data: imperial -> metric -----------------------------------
    # miles -> km
    op.execute(
        "UPDATE ev_charging_session "
        "SET distance_added = distance_added * 1.60934 "
        "WHERE distance_added IS NOT NULL"
    )
    # MPG -> L/100km
    op.execute(
        "UPDATE ev_vehicles "
        "SET ice_fuel_efficiency = 235.215 / ice_fuel_efficiency "
        "WHERE ice_fuel_efficiency IS NOT NULL AND ice_fuel_efficiency > 0"
    )
    # gallons -> liters
    op.execute(
        "UPDATE ev_vehicles "
        "SET ice_fuel_tank_capacity = ice_fuel_tank_capacity * 3.78541 "
        "WHERE ice_fuel_tank_capacity IS NOT NULL"
    )
    # miles -> km
    op.execute(
        "UPDATE ev_statistics "
        "SET total_distance_added = total_distance_added * 1.60934 "
        "WHERE total_distance_added IS NOT NULL"
    )
    # mi/kWh -> km/kWh
    op.execute(
        "UPDATE ev_statistics "
        "SET avg_efficiency = avg_efficiency * 1.60934 "
        "WHERE avg_efficiency IS NOT NULL"
    )

    # --- Migrate efficiency_unit setting to two independent axes ------------
    # Old values: 'us' or 'eu'. New: distance_unit + temp_unit, each 'us' or 'metric'.
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'distance_unit',
               CASE WHEN value = 'eu' THEN 'metric' ELSE 'us' END
        FROM app_settings WHERE key = 'efficiency_unit'
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'temp_unit',
               CASE WHEN value = 'eu' THEN 'metric' ELSE 'us' END
        FROM app_settings WHERE key = 'efficiency_unit'
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute("DELETE FROM app_settings WHERE key = 'efficiency_unit'")


def downgrade() -> None:
    # --- Restore efficiency_unit setting ------------------------------------
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        SELECT 'efficiency_unit',
               CASE WHEN value = 'metric' THEN 'eu' ELSE 'us' END
        FROM app_settings WHERE key = 'distance_unit'
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute("DELETE FROM app_settings WHERE key IN ('distance_unit', 'temp_unit')")

    # --- Convert data back: metric -> imperial ------------------------------
    op.execute(
        "UPDATE ev_statistics "
        "SET avg_efficiency = avg_efficiency / 1.60934 "
        "WHERE avg_efficiency IS NOT NULL"
    )
    op.execute(
        "UPDATE ev_statistics "
        "SET total_distance_added = total_distance_added / 1.60934 "
        "WHERE total_distance_added IS NOT NULL"
    )
    op.execute(
        "UPDATE ev_vehicles "
        "SET ice_fuel_tank_capacity = ice_fuel_tank_capacity / 3.78541 "
        "WHERE ice_fuel_tank_capacity IS NOT NULL"
    )
    op.execute(
        "UPDATE ev_vehicles "
        "SET ice_fuel_efficiency = 235.215 / ice_fuel_efficiency "
        "WHERE ice_fuel_efficiency IS NOT NULL AND ice_fuel_efficiency > 0"
    )
    op.execute(
        "UPDATE ev_charging_session "
        "SET distance_added = distance_added / 1.60934 "
        "WHERE distance_added IS NOT NULL"
    )

    # --- Rename columns back -------------------------------------------------
    op.alter_column(
        "ev_statistics", "avg_efficiency", new_column_name="avg_miles_per_kwh"
    )
    op.alter_column(
        "ev_statistics", "total_distance_added", new_column_name="total_miles_added"
    )
    op.alter_column(
        "ev_vehicles", "ice_fuel_tank_capacity", new_column_name="ice_fuel_tank_gal"
    )
    op.alter_column(
        "ev_vehicles", "ice_fuel_efficiency", new_column_name="ice_mpg"
    )
    op.alter_column(
        "ev_charging_session", "distance_added", new_column_name="miles_added"
    )
