"""Phase 23: Alias tables and session review columns

Create ev_location_gps_aliases and ev_network_name_aliases tables for
location memory and network name resolution. Add duplicate_of_id,
needs_review, and review_type columns to ev_charging_session for
duplicate detection and review queue support.

Revision ID: r23_alias_session_review
Revises: q22_seed_networks
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "r23_alias_session_review"
down_revision = "q22_seed_networks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GPS alias table for location memory
    op.create_table(
        "ev_location_gps_aliases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "location_id",
            sa.Integer,
            sa.ForeignKey("ev_location_lookup.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("latitude", sa.Numeric, nullable=False),
        sa.Column("longitude", sa.Numeric, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Network name alias table for merge-based name resolution
    op.create_table(
        "ev_network_name_aliases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "network_id",
            sa.Integer,
            sa.ForeignKey("ev_charging_networks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias_name", sa.String, nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Session review columns for duplicate detection
    op.add_column(
        "ev_charging_session",
        sa.Column(
            "duplicate_of_id",
            sa.Integer,
            sa.ForeignKey("ev_charging_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ev_charging_session",
        sa.Column(
            "needs_review",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "ev_charging_session",
        sa.Column("review_type", sa.String(20), nullable=True),
    )

    # Partial index for efficient review queue queries
    op.create_index(
        "idx_ev_charging_session_needs_review",
        "ev_charging_session",
        ["needs_review"],
        postgresql_where=sa.text("needs_review = true"),
    )


def downgrade() -> None:
    op.drop_index("idx_ev_charging_session_needs_review", table_name="ev_charging_session")
    op.drop_column("ev_charging_session", "review_type")
    op.drop_column("ev_charging_session", "needs_review")
    op.drop_column("ev_charging_session", "duplicate_of_id")
    op.drop_table("ev_network_name_aliases")
    op.drop_table("ev_location_gps_aliases")
