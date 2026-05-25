"""Drop legacy ICE columns from ev_vehicle_status.

Removes 3 columns not relevant for EVs:

- coolant_temp: Not applicable to EVs (no coolant system)
- engine_speed: Not applicable to EVs (no combustion engine)
- remote_start_countdown: Legacy ICE remote start feature; not relevant for EVs

outside_temperature and cabin_temperature are KEPT — they are the only
time-series source for trip temperature charts (ev_trip_metrics has only
per-trip aggregates).

Revision ID: p30_drop_vstatus_legacy_cols
Revises: p30_squashed_initial
Create Date: 2026-04-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p30_drop_vstatus_legacy_cols"
down_revision: str | Sequence[str] | None = "p30_squashed_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop 3 legacy ICE columns from ev_vehicle_status."""
    op.drop_column("ev_vehicle_status", "coolant_temp")
    op.drop_column("ev_vehicle_status", "engine_speed")
    op.drop_column("ev_vehicle_status", "remote_start_countdown")


def downgrade() -> None:
    """Re-add the 3 dropped columns as nullable Numeric columns."""
    op.add_column(
        "ev_vehicle_status",
        sa.Column("coolant_temp", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ev_vehicle_status",
        sa.Column("engine_speed", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ev_vehicle_status",
        sa.Column("remote_start_countdown", sa.Numeric(), nullable=True),
    )
