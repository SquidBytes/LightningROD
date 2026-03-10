"""add network subscriptions table

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-03-10 15:52:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "i1j2k3l4m5n6"
down_revision: Union[str, Sequence[str], None] = "h1i2j3k4l5m6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ev_network_subscriptions table."""
    op.create_table(
        "ev_network_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("network_id", sa.Integer(), sa.ForeignKey("ev_charging_networks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_rate", sa.Numeric(), nullable=False),
        sa.Column("monthly_fee", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop ev_network_subscriptions table."""
    op.drop_table("ev_network_subscriptions")
