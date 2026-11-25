"""Rename ev_trip_metrics.driving_score to charge_regenerated, Ford's name for it.

Revision ID: rename_charge_regenerated
Revises: charge_type_spellings
Create Date: 2026-11-25
"""
from __future__ import annotations

from alembic import op

revision = "rename_charge_regenerated"
down_revision = "charge_type_spellings"
branch_labels = None
depends_on = None


def _rename(old: str, new: str) -> None:
    with op.batch_alter_table("ev_trip_metrics") as batch:
        batch.alter_column(old, new_column_name=new)


def upgrade() -> None:
    _rename("driving_score", "charge_regenerated")


def downgrade() -> None:
    _rename("charge_regenerated", "driving_score")
