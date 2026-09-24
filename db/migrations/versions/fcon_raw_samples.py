"""Create fcon_raw_samples: verbatim archive of FordConnect responses.

Revision ID: fcon_raw_samples
Revises: relabel_legacy_ha_source
Create Date: 2026-09-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from db.types import JSONStorage

revision = "fcon_raw_samples"
down_revision = "relabel_legacy_ha_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fcon_raw_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("payload", JSONStorage(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column("ingest_schema_version", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_fcon_raw_samples_recorded_at", "fcon_raw_samples", ["recorded_at"]
    )
    op.create_index(
        "idx_fcon_raw_samples_endpoint_recorded",
        "fcon_raw_samples",
        ["endpoint", "recorded_at"],
    )
    op.create_index(
        "uq_fcon_raw_samples_config_fingerprint",
        "fcon_raw_samples",
        ["config_id", "fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_fcon_raw_samples_config_fingerprint", table_name="fcon_raw_samples")
    op.drop_index("idx_fcon_raw_samples_endpoint_recorded", table_name="fcon_raw_samples")
    op.drop_index("idx_fcon_raw_samples_recorded_at", table_name="fcon_raw_samples")
    op.drop_table("fcon_raw_samples")
