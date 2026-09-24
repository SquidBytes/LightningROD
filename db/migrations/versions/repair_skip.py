"""Create repair_skip: review groups a reviewer chose to leave alone.

Keyed by operation slug plus the group's stable key, so preview, census and
apply can all hide a skipped group until the reviewer restores it.

Revision ID: repair_skip
Revises: p38_ha_raw_events
Create Date: 2026-09-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "repair_skip"
down_revision = "p38_ha_raw_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repair_skip",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("group_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation", "group_key", name="uq_repair_skip_operation_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("repair_skip")
