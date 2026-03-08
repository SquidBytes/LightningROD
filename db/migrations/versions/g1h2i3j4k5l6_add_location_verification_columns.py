"""Add is_verified and source_system columns to ev_location_lookup and ev_charging_networks.

Revision ID: g1h2i3j4k5l6
Revises: 208b4ddefdd2
Create Date: 2026-03-08
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'g1h2i3j4k5l6'
down_revision: Union[str, None] = '208b4ddefdd2'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Add verification columns to ev_location_lookup
    op.add_column(
        'ev_location_lookup',
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'ev_location_lookup',
        sa.Column('source_system', sa.String(100), nullable=True),
    )

    # Add verification columns to ev_charging_networks
    op.add_column(
        'ev_charging_networks',
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'ev_charging_networks',
        sa.Column('source_system', sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ev_charging_networks', 'source_system')
    op.drop_column('ev_charging_networks', 'is_verified')
    op.drop_column('ev_location_lookup', 'source_system')
    op.drop_column('ev_location_lookup', 'is_verified')
