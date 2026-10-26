"""Add ev_battery_status.lv_load_status for the 12V battery load state.

Revision ID: lv_load_status
Revises: fcon_raw_samples
Create Date: 2026-09-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "lv_load_status"
down_revision = "fcon_raw_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ev_battery_status", sa.Column("lv_load_status", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("ev_battery_status", "lv_load_status")
