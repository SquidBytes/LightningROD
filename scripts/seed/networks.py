"""Seed module: charging networks (idempotent — only inserts missing entries).

The q22 migration seeds 7 networks (Tesla Supercharger, Electrify America,
ChargePoint, EVgo, EV Connect, IONNA, Rivian Adventure Network).  This module
adds Blink, Home (residential), and Work (employer-provided) if they are
absent, ensuring ≥ 10 rows total.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVChargingNetwork

# Additional demo networks not covered by the q22_seed_charging_networks migration.
# Field conventions match the migration: network_name, cost_per_kwh, color,
# is_verified, source_system, is_free.
_SEED_NETWORKS: list[dict] = [
    {
        "network_name": "Blink",
        "cost_per_kwh": 0.40,
        "color": "#0033A0",
        "is_verified": True,
        "source_system": "seed",
        "is_free": False,
    },
    {
        "network_name": "Home",
        "cost_per_kwh": 0.13,
        "color": "#6C757D",
        "is_verified": True,
        "source_system": "seed",
        "is_free": False,
        "notes": "Residential / personal charging",
    },
    {
        "network_name": "Work",
        "cost_per_kwh": 0.0,
        "color": "#10B981",
        "is_verified": True,
        "source_system": "seed",
        "is_free": True,
        "notes": "Employer-provided workplace charging",
    },
]


async def seed(db: AsyncSession) -> int:
    """Insert demo networks that are not already present.

    Returns the number of rows inserted (0 if all already exist).
    """
    names = [n["network_name"] for n in _SEED_NETWORKS]
    existing = set(
        (
            await db.execute(
                select(EVChargingNetwork.network_name).where(
                    EVChargingNetwork.network_name.in_(names)
                )
            )
        )
        .scalars()
        .all()
    )

    inserted = 0
    for net_data in _SEED_NETWORKS:
        if net_data["network_name"] in existing:
            continue
        db.add(EVChargingNetwork(**net_data))
        inserted += 1

    await db.flush()
    return inserted
