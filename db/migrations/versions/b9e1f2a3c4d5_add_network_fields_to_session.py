"""add network_fields to ev_charging_session

Revision ID: b9e1f2a3c4d5
Revises: 5f531bc75936
Create Date: 2026-03-02 18:59:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9e1f2a3c4d5'
down_revision: Union[str, Sequence[str], None] = '5f531bc75936'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add network_id FK, address, latitude, longitude to ev_charging_session; backfill network_id."""
    # Add new columns
    op.add_column('ev_charging_session', sa.Column('network_id', sa.Integer(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('address', sa.Text(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('latitude', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('longitude', sa.Numeric(), nullable=True))

    # Create FK constraint: network_id -> ev_charging_networks.id, ondelete SET NULL
    op.create_foreign_key(
        'fk_ev_charging_session_network_id',
        'ev_charging_session',
        'ev_charging_networks',
        ['network_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # Create index on network_id for query performance
    op.create_index(
        'idx_ev_charging_session_network_id',
        'ev_charging_session',
        ['network_id'],
    )

    # Backfill network_id by matching session.location_name -> network.network_name
    op.execute(
        """
        UPDATE ev_charging_session
        SET network_id = n.id
        FROM ev_charging_networks n
        WHERE ev_charging_session.location_name = n.network_name
          AND ev_charging_session.network_id IS NULL
        """
    )


def downgrade() -> None:
    """Remove network_id FK, index, and all 4 added columns."""
    op.drop_index('idx_ev_charging_session_network_id', table_name='ev_charging_session')
    op.drop_constraint('fk_ev_charging_session_network_id', 'ev_charging_session', type_='foreignkey')
    op.drop_column('ev_charging_session', 'longitude')
    op.drop_column('ev_charging_session', 'latitude')
    op.drop_column('ev_charging_session', 'address')
    op.drop_column('ev_charging_session', 'network_id')
