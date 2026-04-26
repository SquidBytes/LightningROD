"""Seed module: charger stalls at the 4 demo locations."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVChargerStall, EVLocationLookup

logger = logging.getLogger(__name__)

_STALLS_SPEC = [
    {
        "location_name": "Home",
        "stall_label": "Home L2",
        "charger_type": "L2",
        "rated_kw": 7.4,
        "voltage": 240,
        "amperage": 31,
        "connector_type": "J1772",
        "notes": "Single-port residential L2 EVSE",
        "is_default": True,
    },
    {
        "location_name": "Work",
        "stall_label": "Work L2",
        "charger_type": "L2",
        "rated_kw": 7.7,
        "voltage": 208,
        "amperage": 32,
        "connector_type": "J1772",
        "notes": "Single-port workplace L2 EVSE",
        "is_default": True,
    },
    {
        "location_name": "Tesla Supercharger Downtown",
        "stall_label": "Tesla Supercharger",
        "charger_type": "DCFC",
        "rated_kw": 250,
        "voltage": 400,
        "amperage": None,
        "connector_type": "NACS",
        "notes": "V3 Supercharger, DC fast charge",
        "is_default": False,
    },
    {
        "location_name": "Electrify America Costco",
        "stall_label": "EA DCFC",
        "charger_type": "DCFC",
        "rated_kw": 350,
        "voltage": 800,
        "amperage": None,
        "connector_type": "CCS1",
        "notes": "Electrify America 350 kW DC fast charge",
        "is_default": False,
    },
]


async def _resolve_location_id(db: AsyncSession, name: str) -> int | None:
    return (
        await db.execute(
            select(EVLocationLookup.id).where(EVLocationLookup.location_name == name)
        )
    ).scalar_one_or_none()


async def seed(db: AsyncSession) -> int:
    """Insert demo charger stalls; skip any that already exist by (location_id, connector_type).

    Returns the number of new rows inserted (0–4).
    """
    inserted = 0
    for spec in _STALLS_SPEC:
        loc_id = await _resolve_location_id(db, spec["location_name"])
        if loc_id is None:
            logger.warning(
                "Location %r missing — skipping stall %r",
                spec["location_name"],
                spec["stall_label"],
            )
            continue

        existing = (
            await db.execute(
                select(EVChargerStall.id).where(
                    EVChargerStall.location_id == loc_id,
                    EVChargerStall.connector_type == spec["connector_type"],
                )
            )
        ).scalar_one_or_none()
        if existing:
            continue

        db.add(
            EVChargerStall(
                location_id=loc_id,
                stall_label=spec["stall_label"],
                charger_type=spec["charger_type"],
                rated_kw=spec["rated_kw"],
                voltage=spec["voltage"],
                amperage=spec["amperage"],
                connector_type=spec["connector_type"],
                notes=spec["notes"],
                is_default=spec["is_default"],
            )
        )
        inserted += 1

    await db.flush()
    return inserted
