"""ICE Fuel comparison schema — ICE columns, Fuel price history, Fuel price readings
Add ICE comparison columns to ev_vehicles, create gas_price_history table
with two price tracks, create gas_price_readings staging table for HA sensor
data, and remove deprecated gas_price_per_gallon and vehicle_mpg app_settings.
Revision ID: p20_gas_comparison
Revises: i1j2k3l4m5n6
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa

revision = "p20_gas_comparison"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add ICE comparison columns to ev_vehicles
    op.add_column("ev_vehicles", sa.Column("ice_mpg", sa.Numeric(), nullable=True))
    op.add_column("ev_vehicles", sa.Column("ice_fuel_tank_gal", sa.Numeric(), nullable=True))
    op.add_column("ev_vehicles", sa.Column("ice_label", sa.String(), nullable=True))

    # 2. Create gas_price_history table
    op.create_table(
        "gas_price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("station_price", sa.Numeric(), nullable=True),
        sa.Column("average_price", sa.Numeric(), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("year", "month", name="uq_gas_price_history_year_month"),
    )

    # 3. Create gas_price_readings staging table
    op.create_table(
        "gas_price_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    # 4. Remove deprecated app_settings keys
    op.execute(
        "DELETE FROM app_settings WHERE key IN ('gas_price_per_gallon', 'vehicle_mpg')"
    )


def downgrade() -> None:
    op.drop_table("gas_price_readings")
    op.drop_table("gas_price_history")
    op.drop_column("ev_vehicles", "ice_label")
    op.drop_column("ev_vehicles", "ice_fuel_tank_gal")
    op.drop_column("ev_vehicles", "ice_mpg")
