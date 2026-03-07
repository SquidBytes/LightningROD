"""Add EVSE columns to ev_charging_session and create ev_charger_stalls table.

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-03-06
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Create ev_charger_stalls table first (referenced by FK)
    op.create_table(
        'ev_charger_stalls',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('location_id', sa.Integer(), sa.ForeignKey('ev_location_lookup.id', ondelete='CASCADE'), nullable=False),
        sa.Column('stall_label', sa.String(), nullable=False),
        sa.Column('charger_type', sa.String(10), nullable=True),
        sa.Column('rated_kw', sa.Numeric(), nullable=True),
        sa.Column('voltage', sa.Numeric(), nullable=True),
        sa.Column('amperage', sa.Numeric(), nullable=True),
        sa.Column('connector_type', sa.String(20), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )

    # Add EVSE columns to ev_charging_session
    op.add_column('ev_charging_session', sa.Column('evse_voltage', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('evse_amperage', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('evse_kw', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('evse_energy_kwh', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('evse_max_power_kw', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('charger_rated_kw', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('evse_source', sa.String(20), nullable=True))
    op.add_column('ev_charging_session', sa.Column('stall_id', sa.Integer(), nullable=True))

    # Add FK constraint for stall_id
    op.create_foreign_key(
        'fk_ev_charging_session_stall_id',
        'ev_charging_session',
        'ev_charger_stalls',
        ['stall_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    # Drop FK constraint first
    op.drop_constraint('fk_ev_charging_session_stall_id', 'ev_charging_session', type_='foreignkey')

    # Drop EVSE columns from ev_charging_session
    op.drop_column('ev_charging_session', 'stall_id')
    op.drop_column('ev_charging_session', 'evse_source')
    op.drop_column('ev_charging_session', 'charger_rated_kw')
    op.drop_column('ev_charging_session', 'evse_max_power_kw')
    op.drop_column('ev_charging_session', 'evse_energy_kwh')
    op.drop_column('ev_charging_session', 'evse_kw')
    op.drop_column('ev_charging_session', 'evse_amperage')
    op.drop_column('ev_charging_session', 'evse_voltage')

    # Drop ev_charger_stalls table
    op.drop_table('ev_charger_stalls')
