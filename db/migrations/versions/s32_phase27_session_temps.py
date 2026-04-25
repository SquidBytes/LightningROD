"""Add charging-session thermal context columns.
Adds four nullable NUMERIC columns to ``ev_charging_session`` to capture the
battery and ambient temperatures at the start and end of a charging session.
The ha-fordpass payload only exposes single-value snapshots today
(``batteryTemperature`` on elvehcharging, ``outsidetemp`` sensor state), so the
handler mirrors one value into both ``*_start`` and ``*_end`` columns until
HA emits discrete per-session snapshots. See ``27-01-HA-AUDIT.md`` for the
field-by-field disposition.
No backfill — existing rows stay NULL until a new HA event repopulates them.
Revision ID: s32_phase27_session_temps
Revises: s33_phase27_vehicle_trim_split
Create Date: 2026-04-12
Note: Chained after ``s33_phase27_vehicle_trim_split`` rather than branching from
``s31_battery_gross`` because landed its migration first in the wave.
The revision id keeps the ``s32_`` prefix for naming stability (it is an alembic
revision id, not a sort key).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "s32_phase27_session_temps"
down_revision: str | None = "s33_phase27_vehicle_trim_split"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ev_charging_session",
        sa.Column("battery_temp_start", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ev_charging_session",
        sa.Column("battery_temp_end", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ev_charging_session",
        sa.Column("ambient_temp_start", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ev_charging_session",
        sa.Column("ambient_temp_end", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ev_charging_session", "ambient_temp_end")
    op.drop_column("ev_charging_session", "ambient_temp_start")
    op.drop_column("ev_charging_session", "battery_temp_end")
    op.drop_column("ev_charging_session", "battery_temp_start")
