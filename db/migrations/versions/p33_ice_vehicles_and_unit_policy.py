"""Phase 33: ice_vehicles table + gas-price metric storage retrofit.

Atomically:
  (a) Create ice_vehicles table with partial unique index on is_default
  (b) Backfill ice_vehicles from EVVehicle.ice_* non-NULL rows (mark first as default)
  (c) Drop ev_vehicles.ice_fuel_efficiency / ice_fuel_tank_capacity / ice_label columns
  (d) Multiply gas_price_history.station_price + average_price by GAL_PER_LITER ($/gal -> $/L)
  (e) Multiply gas_price_readings.price by GAL_PER_LITER ($/gal -> $/L)

Cross-dialect: sa.Boolean + sa.Numeric core types, batch_alter_table for
SQLite column drops, parameterized op.execute for data fixups.

Revision ID: p33_ice_vehicles_and_unit_policy
Revises: p31_data_source_foundation
Create Date: 2026-05-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "p33_ice_vehicles_and_unit_policy"
down_revision: str | Sequence[str] | None = "p31_data_source_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Conversion factor: $/gal -> $/L
# Mirrors web.unit_system.GAL_PER_LITER but uses the higher-precision constant
# (1 / 3.78541) to match earlier migration data fixups exactly.
GAL_PER_LITER = 0.26417205235814845


def upgrade() -> None:
    # (a) Create ice_vehicles table
    op.create_table(
        "ice_vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("fuel_efficiency_l_per_100km", sa.Numeric(), nullable=False),
        sa.Column("tank_capacity_l", sa.Numeric(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index: at most one row may be is_default=true
    op.create_index(
        "uq_ice_vehicles_one_default",
        "ice_vehicles",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
        sqlite_where=sa.text("is_default = 1"),
    )

    # (b) Backfill: copy non-NULL EVVehicle.ice_* rows into ice_vehicles.
    # Offline-mode safe — skip the data copy when rendering --sql.
    if not context.is_offline_mode():
        bind = op.get_bind()
        rows = bind.execute(sa.text(
            "SELECT ice_label, ice_fuel_efficiency, ice_fuel_tank_capacity "
            "FROM ev_vehicles "
            "WHERE ice_fuel_efficiency IS NOT NULL"
        )).fetchall()
        if rows:
            ice_tbl = sa.table(
                "ice_vehicles",
                sa.column("label", sa.String),
                sa.column("fuel_efficiency_l_per_100km", sa.Numeric),
                sa.column("tank_capacity_l", sa.Numeric),
                sa.column("is_default", sa.Boolean),
            )
            seed_rows = [
                {
                    "label": r[0] or "Imported ICE Vehicle",
                    "fuel_efficiency_l_per_100km": float(r[1]),
                    "tank_capacity_l": float(r[2]) if r[2] is not None else None,
                    "is_default": (idx == 0),
                }
                for idx, r in enumerate(rows)
            ]
            op.bulk_insert(ice_tbl, seed_rows)

    # (c) Drop legacy ICE columns from ev_vehicles (cross-dialect via batch on SQLite).
    # SQLite online mode uses batch_alter_table (copy-and-move) since the bound
    # connection can reflect the live table. Offline --sql render and PostgreSQL
    # both use direct op.drop_column — SQLite 3.35+ supports ALTER TABLE DROP
    # COLUMN natively, which is what the offline SQL emits, and PG supports it
    # outright.
    bind_dialect = (
        op.get_bind().dialect.name
        if not context.is_offline_mode()
        else context.get_context().dialect.name
    )
    if bind_dialect == "sqlite" and not context.is_offline_mode():
        with op.batch_alter_table("ev_vehicles") as batch_op:
            batch_op.drop_column("ice_fuel_efficiency")
            batch_op.drop_column("ice_fuel_tank_capacity")
            batch_op.drop_column("ice_label")
    else:
        op.drop_column("ev_vehicles", "ice_fuel_efficiency")
        op.drop_column("ev_vehicles", "ice_fuel_tank_capacity")
        op.drop_column("ev_vehicles", "ice_label")

    # (d) + (e) UNIT-02 data fixup: convert $/gal storage to $/L.
    # Multiply existing rows by GAL_PER_LITER. Cross-dialect parameterized SQL.
    for table, columns in (
        ("gas_price_history", ("station_price", "average_price")),
        ("gas_price_readings", ("price",)),
    ):
        for col in columns:
            op.execute(
                sa.text(
                    f"UPDATE {table} SET {col} = {col} * :factor "
                    f"WHERE {col} IS NOT NULL"
                ).bindparams(factor=GAL_PER_LITER)
            )


def downgrade() -> None:
    """No-op: one-way migration per CLAUDE.md no-backwards-compat policy."""
    raise NotImplementedError(
        "p33_ice_vehicles_and_unit_policy is a one-way migration and cannot be downgraded."
    )
