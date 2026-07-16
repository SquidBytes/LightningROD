"""Repair hv_battery_capacity rows wrongly divided by 1000.

Some ha-fordpass installs report capacity already in kWh (141.2); the blind
Wh -> kWh conversion divided those by 1000, storing 0.1412. Corrupted values
are true_kWh/1000, i.e. in [0.01, 0.3]; no real pack is under 1 kWh, so
`< 1.0` cleanly selects corrupted rows. Multiply back by 1000; idempotent.

Revision ID: fix_capacity_kwh_downscaled
Revises: p37_repair_backup
Create Date: 2026-07-16
"""
from __future__ import annotations

from alembic import op

revision = "fix_capacity_kwh_downscaled"
down_revision = "p37_repair_backup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE ev_battery_status "
        "SET hv_battery_capacity = hv_battery_capacity * 1000 "
        "WHERE hv_battery_capacity > 0 AND hv_battery_capacity < 1.0"
    )


def downgrade() -> None:
    # Irreversible data repair: repaired rows can't be distinguished from
    # legitimate kWh values after the fix. No-op.
    pass
