"""add address to ev_location_lookup

Revision ID: d1e2f3a4b5c6
Revises: 5f531bc75936
Create Date: 2026-03-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: str | Sequence[str] | None = 'b9e1f2a3c4d5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add address column to ev_location_lookup."""
    op.add_column('ev_location_lookup', sa.Column('address', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove address column from ev_location_lookup."""
    op.drop_column('ev_location_lookup', 'address')
