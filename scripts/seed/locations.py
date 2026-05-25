"""Seed module: 4 demo locations (Home, Work, public chargers)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVLocationLookup

_SEED_LOCATIONS = [
    {
        "location_name": "Home",
        "latitude": 39.7392,
        "longitude": -104.9903,
        "location_type": "private",
        "is_verified": True,
        "source_system": "seed",
        "network_id": None,
    },
    {
        "location_name": "Work",
        "latitude": 39.7440,
        "longitude": -104.9700,
        "location_type": "private",
        "is_verified": True,
        "source_system": "seed",
        "network_id": None,
    },
    {
        "location_name": "Tesla Supercharger Downtown",
        "latitude": 39.7510,
        "longitude": -104.9890,
        "location_type": "public",
        "is_verified": True,
        "source_system": "seed",
        "network_id": None,  # T6 networks module sets this at runtime
    },
    {
        "location_name": "Electrify America Costco",
        "latitude": 39.7710,
        "longitude": -105.0040,
        "location_type": "public",
        "is_verified": True,
        "source_system": "seed",
        "network_id": None,  # T6 networks module sets this at runtime
    },
]


async def seed(db: AsyncSession) -> int:
    """Insert demo location rows; skip any that already exist by name.

    Returns the number of new rows inserted (0–4).
    """
    names = [r["location_name"] for r in _SEED_LOCATIONS]
    existing = (
        await db.execute(
            select(EVLocationLookup.location_name).where(
                EVLocationLookup.location_name.in_(names)
            )
        )
    ).scalars().all()
    existing_set = set(existing)

    inserted = 0
    for row_data in _SEED_LOCATIONS:
        if row_data["location_name"] in existing_set:
            continue
        db.add(EVLocationLookup(**row_data))
        inserted += 1

    await db.flush()
    return inserted
