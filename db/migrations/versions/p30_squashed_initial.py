"""Squashed initial schema — dialect-portable (PostgreSQL + SQLite).

Consolidates 21 prior migrations into a single dialect-aware initial. The
resulting schema matches the cumulative HEAD that the prior chain produced.
Existing PostgreSQL dev databases must run ``alembic stamp p30_squashed_initial``
once before pulling this migration; see ``db/migrations/README.md``.

Dialect-portability rewrites used throughout:
- ``postgresql.TIMESTAMP(timezone=True)`` -> ``sa.DateTime(timezone=True)``
- ``postgresql.UUID`` -> ``sa.Uuid(as_uuid=True)``
- ``sa.text("NOW()")`` -> ``sa.func.now()``
- ``postgresql.JSONB`` -> ``db.types.JSONStorage``
- partial indexes get dual ``postgresql_where=`` + ``sqlite_where=`` kwargs

A safety check at the top of ``upgrade()`` refuses to apply against a
non-empty unstamped schema, preventing data loss on existing dev databases.

Seed data carried forward to match HEAD on a fresh database:
- ``ev_charging_networks``: 7 rows (the surviving set after upstream cleanup).
- ``app_settings``: 3 ``comparison_*`` keys.

Revision ID: p30_squashed_initial
Revises:
Create Date: 2026-04-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

from db.types import JSONStorage

revision: str = "p30_squashed_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the squashed initial schema."""
    # -----------------------------------------------------------------------
    # Safety check: refuse to apply against a non-empty unstamped schema so
    # an existing dev database isn't blown away by a fresh squash. Operators
    # must `alembic stamp p30_squashed_initial` once on legacy databases
    # before this migration runs (see db/migrations/README.md).
    #
    # Skipped under --sql offline mode: there's no live connection to
    # inspect, and the rendered SQL is for human review only.
    # -----------------------------------------------------------------------
    if not context.is_offline_mode():
        bind = op.get_bind()
        inspector = inspect(bind)
        existing_tables = set(inspector.get_table_names())
        # alembic_version is bookkeeping; if it's the only table, this is the
        # normal case where Alembic has already created its tracking table.
        existing_tables.discard("alembic_version")
        if existing_tables:
            raise RuntimeError(
                "Squashed initial migration refused to run: existing tables "
                "found without an Alembic stamp matching p30_squashed_initial. "
                "If this is an existing dev database, run: "
                "uv run alembic stamp p30_squashed_initial "
                "(see db/migrations/README.md). "
                "If this is a fresh database, drop it and re-run "
                "alembic upgrade head."
            )

    # -----------------------------------------------------------------------
    # Tables (created in dependency order so FKs resolve at CREATE time).
    # -----------------------------------------------------------------------

    # ev_charging_networks (referenced by many others)
    op.create_table(
        "ev_charging_networks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("network_name", sa.String(), nullable=False),
        sa.Column("cost_per_kwh", sa.Numeric(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_free", sa.Boolean(), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ev_location_lookup (references ev_charging_networks)
    op.create_table(
        "ev_location_lookup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("location_name", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("latitude", sa.Numeric(), nullable=True),
        sa.Column("longitude", sa.Numeric(), nullable=True),
        sa.Column("location_type", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("network_id", sa.Integer(), nullable=True),
        sa.Column("cost_per_kwh", sa.Numeric(), nullable=True),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["network_id"], ["ev_charging_networks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ev_charger_stalls (references ev_location_lookup)
    op.create_table(
        "ev_charger_stalls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("stall_label", sa.String(), nullable=False),
        sa.Column("charger_type", sa.String(length=10), nullable=True),
        sa.Column("rated_kw", sa.Numeric(), nullable=True),
        sa.Column("voltage", sa.Numeric(), nullable=True),
        sa.Column("amperage", sa.Numeric(), nullable=True),
        sa.Column("connector_type", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["ev_location_lookup.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ev_charging_session (references ev_charging_networks, ev_charger_stalls,
    # and itself for duplicate_of_id)
    op.create_table(
        "ev_charging_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("charge_type", sa.String(), nullable=True),
        sa.Column("location_name", sa.String(), nullable=True),
        sa.Column("location_type", sa.String(length=20), nullable=True),
        sa.Column("network_id", sa.Integer(), nullable=True),
        sa.Column("is_free", sa.Boolean(), nullable=True),
        sa.Column("plug_status", sa.String(), nullable=True),
        sa.Column("charging_status", sa.String(), nullable=True),
        sa.Column("station_status", sa.String(), nullable=True),
        sa.Column("charging_voltage", sa.Numeric(), nullable=True),
        sa.Column("charging_amperage", sa.Numeric(), nullable=True),
        sa.Column("charging_kw", sa.Numeric(), nullable=True),
        sa.Column("session_start_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_end_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_end_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("charge_duration_seconds", sa.Numeric(), nullable=True),
        sa.Column("plugged_in_duration_seconds", sa.Numeric(), nullable=True),
        sa.Column("start_soc", sa.Numeric(), nullable=True),
        sa.Column("end_soc", sa.Numeric(), nullable=True),
        sa.Column("energy_kwh", sa.Numeric(), nullable=True),
        sa.Column("cost", sa.Numeric(), nullable=True),
        sa.Column("cost_without_overrides", sa.Numeric(), nullable=True),
        sa.Column("cost_source", sa.String(length=20), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Numeric(), nullable=True),
        sa.Column("longitude", sa.Numeric(), nullable=True),
        sa.Column("max_power", sa.Numeric(), nullable=True),
        sa.Column("min_power", sa.Numeric(), nullable=True),
        sa.Column("distance_added", sa.Numeric(), nullable=True),
        sa.Column("evse_voltage", sa.Numeric(), nullable=True),
        sa.Column("evse_amperage", sa.Numeric(), nullable=True),
        sa.Column("evse_kw", sa.Numeric(), nullable=True),
        sa.Column("evse_energy_kwh", sa.Numeric(), nullable=True),
        sa.Column("evse_max_power_kw", sa.Numeric(), nullable=True),
        sa.Column("charger_rated_kw", sa.Numeric(), nullable=True),
        sa.Column("evse_source", sa.String(length=20), nullable=True),
        sa.Column("stall_id", sa.Integer(), nullable=True),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("review_type", sa.String(length=20), nullable=True),
        sa.Column("battery_temp_start", sa.Numeric(), nullable=True),
        sa.Column("battery_temp_end", sa.Numeric(), nullable=True),
        sa.Column("ambient_temp_start", sa.Numeric(), nullable=True),
        sa.Column("ambient_temp_end", sa.Numeric(), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("original_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["ev_charging_networks.id"],
            name="fk_ev_charging_session_network_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["stall_id"],
            ["ev_charger_stalls.id"],
            name="fk_ev_charging_session_stall_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"],
            ["ev_charging_session.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_ev_charging_session_session_id"),
    )
    op.create_index(
        "idx_ev_charging_session_device_id",
        "ev_charging_session",
        ["device_id"],
        unique=False,
    )
    # Partial index — dual-dialect kwargs. SQLite stores Boolean as 0/1.
    op.create_index(
        "idx_ev_charging_session_is_complete",
        "ev_charging_session",
        ["is_complete"],
        unique=False,
        postgresql_where=sa.text("is_complete = true"),
        sqlite_where=sa.text("is_complete = 1"),
    )
    op.create_index(
        "idx_ev_charging_session_session_start_utc",
        "ev_charging_session",
        ["session_start_utc"],
        unique=False,
    )
    op.create_index(
        "idx_ev_charging_session_source_system",
        "ev_charging_session",
        ["source_system"],
        unique=False,
    )
    op.create_index(
        "idx_ev_charging_session_network_id",
        "ev_charging_session",
        ["network_id"],
        unique=False,
    )
    # Partial index from r23_alias_tables_and_session_review — dual kwargs.
    op.create_index(
        "idx_ev_charging_session_needs_review",
        "ev_charging_session",
        ["needs_review"],
        unique=False,
        postgresql_where=sa.text("needs_review = true"),
        sqlite_where=sa.text("needs_review = 1"),
    )

    # ev_battery_status
    op.create_table(
        "ev_battery_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hv_battery_soc", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_actual_soc", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_voltage", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_amperage", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_kw", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_capacity", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_range", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_max_range", sa.Numeric(), nullable=True),
        sa.Column("hv_battery_temperature", sa.Numeric(), nullable=True),
        sa.Column("lv_battery_level", sa.Numeric(), nullable=True),
        sa.Column("lv_battery_voltage", sa.Numeric(), nullable=True),
        sa.Column("motor_voltage", sa.Numeric(), nullable=True),
        sa.Column("motor_amperage", sa.Numeric(), nullable=True),
        sa.Column("motor_kw", sa.Numeric(), nullable=True),
        sa.Column("performance_status", sa.String(), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("original_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ev_battery_status_device_id",
        "ev_battery_status",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "idx_ev_battery_status_recorded_at",
        "ev_battery_status",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        "idx_ev_battery_status_source_system",
        "ev_battery_status",
        ["source_system"],
        unique=False,
    )

    # ev_location
    op.create_table(
        "ev_location",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Numeric(), nullable=True),
        sa.Column("longitude", sa.Numeric(), nullable=True),
        sa.Column("gps_accuracy", sa.Numeric(), nullable=True),
        sa.Column("altitude", sa.Numeric(), nullable=True),
        sa.Column("compass_direction", sa.String(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("location_type", sa.String(), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("original_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ev_location_device_id",
        "ev_location",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "idx_ev_location_device_recorded_at",
        "ev_location",
        ["device_id", "recorded_at"],
        unique=False,
    )
    op.create_index(
        "idx_ev_location_recorded_at",
        "ev_location",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        "idx_ev_location_source_system",
        "ev_location",
        ["source_system"],
        unique=False,
    )

    # ev_statistics (column names reflect the s30 metric-canonical rename:
    # total_distance_added (km), avg_efficiency (km/kWh)).
    op.create_table(
        "ev_statistics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("total_sessions", sa.Integer(), nullable=True),
        sa.Column("total_energy_kwh", sa.Numeric(), nullable=True),
        sa.Column("total_cost", sa.Numeric(), nullable=True),
        sa.Column("total_distance_added", sa.Numeric(), nullable=True),
        sa.Column("avg_session_duration_seconds", sa.Numeric(), nullable=True),
        sa.Column("avg_energy_per_session_kwh", sa.Numeric(), nullable=True),
        sa.Column("avg_cost_per_kwh", sa.Numeric(), nullable=True),
        sa.Column("avg_efficiency", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ev_trip_metrics
    op.create_table(
        "ev_trip_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("distance", sa.Numeric(), nullable=True),
        sa.Column("duration", sa.Numeric(), nullable=True),
        sa.Column("energy_consumed", sa.Numeric(), nullable=True),
        sa.Column("efficiency", sa.Numeric(), nullable=True),
        sa.Column("range_regenerated", sa.Numeric(), nullable=True),
        sa.Column("ambient_temp", sa.Numeric(), nullable=True),
        sa.Column("cabin_temp", sa.Numeric(), nullable=True),
        sa.Column("outside_air_temp", sa.Numeric(), nullable=True),
        sa.Column("driving_score", sa.Numeric(), nullable=True),
        sa.Column("speed_score", sa.Numeric(), nullable=True),
        sa.Column("acceleration_score", sa.Numeric(), nullable=True),
        sa.Column("deceleration_score", sa.Numeric(), nullable=True),
        sa.Column("start_location_id", sa.Integer(), nullable=True),
        sa.Column("end_location_id", sa.Integer(), nullable=True),
        sa.Column("electrical_efficiency", sa.Numeric(), nullable=True),
        sa.Column("brake_torque", sa.Numeric(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("original_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ev_trip_metrics_device_id",
        "ev_trip_metrics",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "idx_ev_trip_metrics_source_system",
        "ev_trip_metrics",
        ["source_system"],
        unique=False,
    )
    op.create_index(
        "idx_ev_trip_metrics_start_time",
        "ev_trip_metrics",
        ["start_time"],
        unique=False,
    )

    # ev_vehicle_status (uses JSONStorage for cross-dialect JSON columns).
    op.create_table(
        "ev_vehicle_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer", sa.Numeric(), nullable=True),
        sa.Column("speed", sa.Numeric(), nullable=True),
        sa.Column("accelerator_position", sa.Numeric(), nullable=True),
        sa.Column("brake_status", sa.String(), nullable=True),
        sa.Column("gear_position", sa.String(), nullable=True),
        sa.Column("parking_brake", sa.String(), nullable=True),
        sa.Column("ignition_status", sa.String(), nullable=True),
        sa.Column("remote_start_status", sa.String(), nullable=True),
        sa.Column("coolant_temp", sa.Numeric(), nullable=True),
        sa.Column("torque_at_transmission", sa.Numeric(), nullable=True),
        sa.Column("door_lock_status", JSONStorage(), nullable=True),
        sa.Column("tire_pressure", JSONStorage(), nullable=True),
        sa.Column("indicators", JSONStorage(), nullable=True),
        sa.Column("brake_torque", sa.Numeric(), nullable=True),
        sa.Column("wheel_torque_status", sa.String(), nullable=True),
        sa.Column("yaw_rate", sa.Numeric(), nullable=True),
        sa.Column("acceleration", sa.Numeric(), nullable=True),
        sa.Column("engine_speed", sa.Numeric(), nullable=True),
        sa.Column("outside_temperature", sa.Numeric(), nullable=True),
        sa.Column("cabin_temperature", sa.Numeric(), nullable=True),
        sa.Column("deep_sleep_status", sa.String(), nullable=True),
        sa.Column("device_connectivity", sa.String(), nullable=True),
        sa.Column("evcc_status", sa.String(), nullable=True),
        sa.Column("seatbelt_status", sa.String(), nullable=True),
        sa.Column("remote_start_countdown", sa.Numeric(), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("original_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ev_vehicle_status_device_id",
        "ev_vehicle_status",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "idx_ev_vehicle_status_recorded_at",
        "ev_vehicle_status",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        "idx_ev_vehicle_status_source_system",
        "ev_vehicle_status",
        ["source_system"],
        unique=False,
    )

    # app_settings (added by c9345e830aab; only the 3 comparison_* keys
    # remain on a fresh DB at HEAD because p20_gas_comparison deletes
    # gas_price_per_gallon and vehicle_mpg).
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    # ev_vehicles (h1i2j3k4l5m6 + p20 ICE columns + s30 metric rename
    # + s31 gross capacity + s33 trim/battery_option split).
    op.create_table(
        "ev_vehicles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("make", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("trim_level", sa.String(), nullable=True),
        sa.Column("battery_option", sa.String(), nullable=True),
        sa.Column("battery_capacity_kwh", sa.Numeric(), nullable=True),
        sa.Column("battery_gross_capacity_kwh", sa.Numeric(), nullable=True),
        sa.Column("vin", sa.String(), nullable=True),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column("ice_fuel_efficiency", sa.Numeric(), nullable=True),
        sa.Column("ice_fuel_tank_capacity", sa.Numeric(), nullable=True),
        sa.Column("ice_label", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vin"),
        sa.UniqueConstraint("device_id"),
    )

    # ev_network_subscriptions
    op.create_table(
        "ev_network_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("network_id", sa.Integer(), nullable=False),
        sa.Column("member_rate", sa.Numeric(), nullable=False),
        sa.Column(
            "monthly_fee",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["network_id"], ["ev_charging_networks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # gas_price_history
    op.create_table(
        "gas_price_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("station_price", sa.Numeric(), nullable=True),
        sa.Column("average_price", sa.Numeric(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year", "month", name="uq_gas_price_history_year_month"),
    )

    # gas_price_readings (HA staging table).
    op.create_table(
        "gas_price_readings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ev_location_gps_aliases (r23).
    op.create_table(
        "ev_location_gps_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("latitude", sa.Numeric(), nullable=False),
        sa.Column("longitude", sa.Numeric(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["ev_location_lookup.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ev_network_name_aliases (r23).
    op.create_table(
        "ev_network_name_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("network_id", sa.Integer(), nullable=False),
        sa.Column("alias_name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["network_id"], ["ev_charging_networks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias_name"),
    )

    # -----------------------------------------------------------------------
    # Seed data — final HEAD-equivalent rows on a fresh DB.
    # Uses op.bulk_insert (SQLAlchemy Core) so it emits dialect-correct SQL
    # on both PostgreSQL and SQLite.
    # -----------------------------------------------------------------------

    # 7 charging networks (q22_seed_charging_networks; the 14 networks
    # seeded by c9345e830aab were all deleted by 208b4ddefdd2 on a fresh
    # DB because none had associated sessions or locations).
    ev_charging_networks_tbl = sa.table(
        "ev_charging_networks",
        sa.column("network_name", sa.String),
        sa.column("cost_per_kwh", sa.Numeric),
        sa.column("color", sa.String),
        sa.column("is_verified", sa.Boolean),
        sa.column("source_system", sa.String),
        sa.column("is_free", sa.Boolean),
    )
    op.bulk_insert(
        ev_charging_networks_tbl,
        [
            {
                "network_name": "Tesla Supercharger",
                "cost_per_kwh": 0.42,
                "color": "#CC0000",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
            {
                "network_name": "Electrify America",
                "cost_per_kwh": 0.48,
                "color": "#00A94F",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
            {
                "network_name": "ChargePoint",
                "cost_per_kwh": 0.35,
                "color": "#FF6B2D",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
            {
                "network_name": "EVgo",
                "cost_per_kwh": 0.39,
                "color": "#00AEEF",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
            {
                "network_name": "EV Connect",
                "cost_per_kwh": 0.30,
                "color": "#4CAF50",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
            {
                "network_name": "IONNA",
                "cost_per_kwh": 0.40,
                "color": "#1A1A2E",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
            {
                "network_name": "Rivian Adventure Network",
                "cost_per_kwh": 0.35,
                "color": "#517B50",
                "is_verified": True,
                "source_system": "seed",
                "is_free": False,
            },
        ],
    )

    # 3 app_settings keys (the comparison_* set; the other two seeded
    # keys, gas_price_per_gallon and vehicle_mpg, were deleted by p20).
    app_settings_tbl = sa.table(
        "app_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
    )
    op.bulk_insert(
        app_settings_tbl,
        [
            {"key": "comparison_gas_enabled", "value": "true"},
            {"key": "comparison_network_enabled", "value": "true"},
            {"key": "comparison_section_visible", "value": "true"},
        ],
    )


def downgrade() -> None:
    """No-op: squashed initial migrations are intentionally one-way."""
    raise NotImplementedError(
        "p30_squashed_initial is a one-way squash and cannot be downgraded. "
        "To revert, drop the database and migrate to a pre-squash commit."
    )
