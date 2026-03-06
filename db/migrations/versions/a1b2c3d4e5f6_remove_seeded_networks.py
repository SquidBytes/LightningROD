"""Remove pre-seeded networks that have no linked sessions.

Networks are now created on-demand from data. This migration cleans up
the networks that were seeded by the phase04 migration but never used.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-03-06
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Delete networks that have zero charging sessions and zero locations
    op.execute(
        sa.text("""
            DELETE FROM ev_charging_networks
            WHERE id NOT IN (
                SELECT DISTINCT network_id FROM ev_charging_sessions
                WHERE network_id IS NOT NULL
            )
            AND id NOT IN (
                SELECT DISTINCT network_id FROM ev_location_lookup
                WHERE network_id IS NOT NULL
            )
        """)
    )


def downgrade() -> None:
    # No-op: we cannot know which networks were originally seeded vs user-created
    pass
