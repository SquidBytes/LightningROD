"""Create repair_backup: pre-repair row snapshots grouped by run_id.

Each data-repair apply snapshots the affected rows here first so the run
can be restored later. row_json holds the full serialized row.

Revision ID: p37_repair_backup
Revises: fix_capacity_wh_to_kwh
Create Date: 2026-07-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from db.types import JSONStorage

revision = "p37_repair_backup"
down_revision = "fix_capacity_wh_to_kwh"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repair_backup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("row_pk", sa.Integer(), nullable=False),
        sa.Column("row_json", JSONStorage(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_repair_backup_run_id", "repair_backup", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_repair_backup_run_id", table_name="repair_backup")
    op.drop_table("repair_backup")
