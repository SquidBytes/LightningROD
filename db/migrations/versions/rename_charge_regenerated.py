"""Rename ev_trip_metrics.driving_score to charge_regenerated, Ford's name for it.

Revision ID: rename_charge_regenerated
Revises: charge_type_spellings
Create Date: 2026-11-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from db.types import JSONStorage

revision = "rename_charge_regenerated"
down_revision = "charge_type_spellings"
branch_labels = None
depends_on = None

_backups = sa.table(
    "repair_backup",
    sa.column("id", sa.Integer),
    sa.column("table_name", sa.String),
    sa.column("row_json", JSONStorage),
)


def rename_snapshot_key(conn, old: str, new: str) -> None:
    """Data Repair snapshots store rows by column name; restore needs the current one."""
    rows = conn.execute(
        sa.select(_backups.c.id, _backups.c.row_json).where(
            _backups.c.table_name == "ev_trip_metrics"
        )
    ).all()
    for pk, data in rows:
        if old in data:
            data = {new if k == old else k: v for k, v in data.items()}
            conn.execute(_backups.update().where(_backups.c.id == pk).values(row_json=data))


def _rename(old: str, new: str) -> None:
    with op.batch_alter_table("ev_trip_metrics") as batch:
        batch.alter_column(old, new_column_name=new)
    rename_snapshot_key(op.get_bind(), old, new)


def upgrade() -> None:
    _rename("driving_score", "charge_regenerated")


def downgrade() -> None:
    _rename("charge_regenerated", "driving_score")
