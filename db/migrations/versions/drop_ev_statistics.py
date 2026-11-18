"""Drop the unused ev_statistics table.

Revision ID: drop_ev_statistics
Revises: charge_type_ac_dc
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "drop_ev_statistics"
down_revision = "charge_type_ac_dc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("ev_statistics")


def downgrade() -> None:
    op.create_table(
        "ev_statistics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("total_sessions", sa.Integer(), nullable=True),
        sa.Column("total_energy_kwh", sa.Numeric(), nullable=True),
        sa.Column("total_cost", sa.Numeric(), nullable=True),
        sa.Column("total_distance_added", sa.Numeric(), nullable=True),
        sa.Column("avg_session_duration_seconds", sa.Numeric(), nullable=True),
        sa.Column("avg_energy_per_session_kwh", sa.Numeric(), nullable=True),
        sa.Column("avg_cost_per_kwh", sa.Numeric(), nullable=True),
        sa.Column("avg_efficiency", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
