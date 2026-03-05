"""add cost hierarchy columns

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-03-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ev_location_lookup', sa.Column('cost_per_kwh', sa.Numeric(), nullable=True))
    op.add_column('ev_charging_session', sa.Column('estimated_cost', sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column('ev_charging_session', 'estimated_cost')
    op.drop_column('ev_location_lookup', 'cost_per_kwh')
