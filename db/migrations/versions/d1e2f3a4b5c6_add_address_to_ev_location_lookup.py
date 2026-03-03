"""add address to ev_location_lookup

Revision ID: d1e2f3a4b5c6
Revises: 5f531bc75936
Create Date: 2026-03-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = '5f531bc75936'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add address column to ev_location_lookup."""
    op.add_column('ev_location_lookup', sa.Column('address', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove address column from ev_location_lookup."""
    op.drop_column('ev_location_lookup', 'address')
