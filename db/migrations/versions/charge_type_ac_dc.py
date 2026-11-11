"""Collapse AC_CHARGING/DC_CHARGING charge types to AC/DC.

The shared charger-type normalizer now maps these spellings like every other
variant; rows written before it did keep the raw code. Idempotent.

Revision ID: charge_type_ac_dc
Revises: lv_load_status
Create Date: 2026-11-11
"""
from __future__ import annotations

from alembic import op

revision = "charge_type_ac_dc"
down_revision = "lv_load_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE ev_charging_session SET charge_type = 'AC' WHERE charge_type = 'AC_CHARGING'"
    )
    op.execute(
        "UPDATE ev_charging_session SET charge_type = 'DC' WHERE charge_type = 'DC_CHARGING'"
    )


def downgrade() -> None:
    # Collapsed rows can't be told apart from rows that were AC/DC already. No-op.
    pass
