"""Add ev_vehicles table with data backfill from existing device_ids.

Revision ID: h1i2j3k4l5m6
Revises: g1h2i3j4k5l6
Create Date: 2026-03-09
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'h1i2j3k4l5m6'
down_revision: Union[str, None] = 'g1h2i3j4k5l6'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # 1. Create ev_vehicles table
    op.create_table(
        'ev_vehicles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('make', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('trim', sa.String(), nullable=True),
        sa.Column('battery_capacity_kwh', sa.Numeric(), nullable=True),
        sa.Column('vin', sa.String(), nullable=True),
        sa.Column('device_id', sa.String(), nullable=False),
        sa.Column('source_system', sa.String(100), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.UniqueConstraint('vin'),
        sa.UniqueConstraint('device_id'),
    )

    # 2. Backfill: create vehicle records from existing session device_ids
    conn = op.get_bind()

    # Find a real device_id (not synthetic ones)
    result = conn.execute(sa.text(
        "SELECT DISTINCT device_id FROM ev_charging_session "
        "WHERE device_id NOT IN ('manual', 'csv_import', 'unknown') "
        "ORDER BY device_id LIMIT 1"
    ))
    real_device_ids = [row[0] for row in result]

    if real_device_ids:
        # Create vehicle for the real device_id (likely the VIN)
        device_id = real_device_ids[0]
        conn.execute(sa.text(
            "INSERT INTO ev_vehicles (display_name, device_id, vin, source_system) "
            "VALUES (:name, :did, :vin, 'migration')"
        ), {"name": "My Vehicle", "did": device_id, "vin": device_id})

        # Get the new vehicle's ID
        vid_result = conn.execute(sa.text(
            "SELECT id FROM ev_vehicles WHERE device_id = :did"
        ), {"did": device_id})
        vehicle_id = vid_result.scalar()

        # Set as active vehicle in app_settings
        conn.execute(sa.text(
            "INSERT INTO app_settings (key, value) VALUES ('active_vehicle_id', :vid) "
            "ON CONFLICT (key) DO UPDATE SET value = :vid"
        ), {"vid": str(vehicle_id)})

        # Reassign manual/csv_import/unknown sessions to this vehicle's device_id
        conn.execute(sa.text(
            "UPDATE ev_charging_session SET device_id = :did "
            "WHERE device_id IN ('manual', 'csv_import', 'unknown')"
        ), {"did": device_id})
    else:
        # No real device_ids -- create a placeholder vehicle
        conn.execute(sa.text(
            "INSERT INTO ev_vehicles (display_name, device_id, source_system) "
            "VALUES ('My Vehicle', 'default', 'migration')"
        ))
        vid_result = conn.execute(sa.text(
            "SELECT id FROM ev_vehicles WHERE device_id = 'default'"
        ))
        vehicle_id = vid_result.scalar()

        # Set as active vehicle
        conn.execute(sa.text(
            "INSERT INTO app_settings (key, value) VALUES ('active_vehicle_id', :vid) "
            "ON CONFLICT (key) DO UPDATE SET value = :vid"
        ), {"vid": str(vehicle_id)})

        # Point all synthetic sessions to this default device_id
        conn.execute(sa.text(
            "UPDATE ev_charging_session SET device_id = 'default' "
            "WHERE device_id IN ('manual', 'csv_import', 'unknown')"
        ))


def downgrade() -> None:
    # Remove active_vehicle_id setting
    op.execute("DELETE FROM app_settings WHERE key = 'active_vehicle_id'")
    # Drop the vehicles table
    op.drop_table('ev_vehicles')
