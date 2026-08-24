"""Create ha_raw_events: verbatim archive of incoming Home Assistant events.

Every state_changed event that reaches ingestion is stored whole before the
typed mapping runs, so trip fields that mapping drops stay recoverable.

Revision ID: p38_ha_raw_events
Revises: fix_capacity_kwh_downscaled
Create Date: 2026-08-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from db.types import JSONStorage

revision = "p38_ha_raw_events"
down_revision = "fix_capacity_kwh_downscaled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ha_raw_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("slug", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("payload", JSONStorage(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("config_id", sa.Integer(), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ha_raw_events_recorded_at", "ha_raw_events", ["recorded_at"]
    )
    op.create_index(
        "idx_ha_raw_events_slug_recorded", "ha_raw_events", ["slug", "recorded_at"]
    )
    op.create_index(
        "uq_ha_raw_events_entity_recorded",
        "ha_raw_events",
        ["entity_id", "recorded_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_ha_raw_events_entity_recorded", table_name="ha_raw_events")
    op.drop_index("idx_ha_raw_events_slug_recorded", table_name="ha_raw_events")
    op.drop_index("idx_ha_raw_events_recorded_at", table_name="ha_raw_events")
    op.drop_table("ha_raw_events")
