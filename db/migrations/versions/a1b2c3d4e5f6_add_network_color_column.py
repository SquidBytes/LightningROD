"""add network color column

Revision ID: a1b2c3d4e5f6
Revises: c9345e830aab
Create Date: 2026-03-01 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c9345e830aab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add color column to ev_charging_networks."""
    op.add_column(
        "ev_charging_networks",
        sa.Column("color", sa.String(length=7), nullable=True),
    )


def downgrade() -> None:
    """Remove color column from ev_charging_networks."""
    op.drop_column("ev_charging_networks", "color")
