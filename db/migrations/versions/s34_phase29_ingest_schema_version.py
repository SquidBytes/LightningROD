"""Add ingest_schema_version column to unit-ful tables.
Adds a nullable SMALLINT column to the three tables that carry unit-ful
numeric data handled by the ha-fordpass adapter introduced in :
ev_trip_metrics
ev_charging_session
ev_battery_status
This migration is ADDITIVE ONLY. No row mutation, no backfill, no
data-correction statements. Existing rows (any value ingested since
2026-03-21 commit abd736b) stay NULL — interpreted by downstream code as
"pre-v2 suspect era". New writes by the adapter mark the column as 2.
The companion data-recovery phase (, deferred) will use this column as
the selector for reverse-conversion: `WHERE ingest_schema_version IS NULL`.
Revision ID: s34_phase29_schema_version
Revises: s32_phase27_session_temps
Create Date: 2026-04-19
Note: Revision id is shortened from the plan-authored
``s34_phase29_ingest_schema_version`` (33 chars) to
``s34_phase29_schema_version`` (26 chars) to fit the
``alembic_version.version_num`` VARCHAR(32) column width — same constraint
that forced to rename its migration (see STATE.md decision log).
The filename keeps the ``s34_phase29_ingest_schema_version.py`` form the plan
specified; only the internal revision id changed.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "s34_phase29_schema_version"
down_revision: str | None = "s32_phase27_session_temps"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ev_trip_metrics",
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "ev_charging_session",
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "ev_battery_status",
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ev_battery_status", "ingest_schema_version")
    op.drop_column("ev_charging_session", "ingest_schema_version")
    op.drop_column("ev_trip_metrics", "ingest_schema_version")
