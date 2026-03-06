import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings, EVChargerStall, EVChargingNetwork, EVLocationLookup

# Predefined EV charging networks with brand-accurate colors
PREDEFINED_NETWORKS = [
    {"name": "Tesla Supercharger", "color": "#E31937", "cost_per_kwh": 0.35, "is_free": False},
    {"name": "Electrify America", "color": "#00B140", "cost_per_kwh": 0.48, "is_free": False},
    {"name": "ChargePoint", "color": "#00A4E4", "cost_per_kwh": 0.39, "is_free": False},
    {"name": "EVgo", "color": "#F7941D", "cost_per_kwh": 0.35, "is_free": False},
    {"name": "Blink", "color": "#0072CE", "cost_per_kwh": 0.49, "is_free": False},
    {"name": "Flo", "color": "#6CBE45", "cost_per_kwh": 0.35, "is_free": False},
    {"name": "Ford BlueOval", "color": "#003478", "cost_per_kwh": 0.33, "is_free": False},
    {"name": "Rivian Adventure Network", "color": "#4DB848", "cost_per_kwh": 0.35, "is_free": False},
    {"name": "Shell Recharge", "color": "#FFD500", "cost_per_kwh": 0.39, "is_free": False},
    {"name": "BP Pulse", "color": "#009B3A", "cost_per_kwh": 0.36, "is_free": False},
    {"name": "Home", "color": "#6366F1", "cost_per_kwh": 0.12, "is_free": True},
]

# Derived color lookup for backward compatibility
NETWORK_COLORS = {n["name"]: n["color"] for n in PREDEFINED_NETWORKS}
DEFAULT_COLOR = "#6B7280"  # gray-500


async def get_all_networks(db: AsyncSession) -> list[EVChargingNetwork]:
    """Return all charging networks ordered by network_name."""
    result = await db.execute(
        select(EVChargingNetwork).order_by(EVChargingNetwork.network_name)
    )
    return list(result.scalars().all())


async def resolve_network(
    db: AsyncSession,
    network_id: Optional[int] = None,
    network_name: Optional[str] = None,
) -> Optional[int]:
    """Resolve a network to its ID. Accepts ID directly or name for lookup/auto-create.

    Priority: network_id (if truthy) > network_name lookup > auto-create from name.
    Returns None if neither is provided or name is empty.
    """
    if network_id:
        return network_id

    if not network_name or not network_name.strip():
        return None

    name = network_name.strip()

    # Try case-insensitive match against existing networks
    from sqlalchemy import func
    result = await db.execute(
        select(EVChargingNetwork).where(
            func.lower(EVChargingNetwork.network_name) == name.lower()
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing.id

    # Auto-create new network with defaults
    resolved_color = NETWORK_COLORS.get(name, DEFAULT_COLOR)
    new_net = EVChargingNetwork(
        network_name=name,
        is_free=False,
        color=resolved_color,
    )
    db.add(new_net)
    await db.flush()  # get the ID without committing
    return new_net.id


async def create_network(
    db: AsyncSession,
    name: str,
    cost_per_kwh: Optional[float],
    is_free: bool,
    color: Optional[str],
) -> EVChargingNetwork:
    """Create a new charging network row.

    If color is None or empty, auto-assign from NETWORK_COLORS lookup by name,
    falling back to DEFAULT_COLOR.
    """
    resolved_color = color if color else NETWORK_COLORS.get(name, DEFAULT_COLOR)
    network = EVChargingNetwork(
        network_name=name,
        cost_per_kwh=cost_per_kwh,
        is_free=is_free,
        color=resolved_color,
    )
    db.add(network)
    await db.commit()
    await db.refresh(network)
    return network


async def update_network(
    db: AsyncSession,
    network_id: int,
    name: str,
    cost_per_kwh: Optional[float],
    is_free: bool,
    color: Optional[str],
) -> Optional[EVChargingNetwork]:
    """Update all fields of an existing charging network.

    Returns the updated network or None if not found.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if network is None:
        return None
    network.network_name = name
    network.cost_per_kwh = cost_per_kwh
    network.is_free = is_free
    network.color = color if color else NETWORK_COLORS.get(name, DEFAULT_COLOR)
    await db.commit()
    await db.refresh(network)
    return network


async def delete_network(db: AsyncSession, network_id: int) -> bool:
    """Delete a charging network by id.

    Returns True if deleted, False if not found.
    """
    result = await db.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == network_id)
    )
    network = result.scalar_one_or_none()
    if network is None:
        return False
    await db.delete(network)
    await db.commit()
    return True


async def seed_predefined_networks(db: AsyncSession) -> int:
    """Seed predefined networks into DB. Matches by name — skips existing.

    Also discovers unique location_name values from sessions and creates
    network rows for any not already in the DB.

    Returns count of networks added.
    """
    existing = await get_all_networks(db)
    existing_names = {n.network_name for n in existing}
    added = 0

    # Seed predefined networks
    for pn in PREDEFINED_NETWORKS:
        if pn["name"] not in existing_names:
            network = EVChargingNetwork(
                network_name=pn["name"],
                color=pn["color"],
                cost_per_kwh=pn["cost_per_kwh"],
                is_free=pn["is_free"] or False,
            )
            db.add(network)
            existing_names.add(pn["name"])
            added += 1

    # Discover networks from session data
    from db.models.charging_session import EVChargingSession

    result = await db.execute(
        select(EVChargingSession.location_name)
        .where(EVChargingSession.location_name.isnot(None))
        .distinct()
    )
    session_networks = {row[0] for row in result.all()}

    for name in session_networks:
        if name and name not in existing_names:
            network = EVChargingNetwork(
                network_name=name,
                color=NETWORK_COLORS.get(name, DEFAULT_COLOR),
                is_free=False,
            )
            db.add(network)
            existing_names.add(name)
            added += 1

    await db.commit()
    return added


async def get_locations_for_network(
    db: AsyncSession, network_id: int
) -> list[EVLocationLookup]:
    """Return all locations linked to a specific network."""
    result = await db.execute(
        select(EVLocationLookup)
        .where(EVLocationLookup.network_id == network_id)
        .order_by(EVLocationLookup.location_name)
    )
    return list(result.scalars().all())


async def create_location(
    db: AsyncSession,
    network_id: int,
    name: str,
    location_type: Optional[str] = None,
    notes: Optional[str] = None,
    address: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    cost_per_kwh: Optional[float] = None,
) -> EVLocationLookup:
    """Create a new location linked to a network."""
    loc = EVLocationLookup(
        location_name=name,
        network_id=network_id,
        location_type=location_type,
        notes=notes,
        address=address,
        latitude=latitude,
        longitude=longitude,
        cost_per_kwh=cost_per_kwh,
    )
    db.add(loc)
    await db.commit()
    await db.refresh(loc)
    return loc


async def update_location(
    db: AsyncSession,
    location_id: int,
    name: str,
    location_type: Optional[str] = None,
    notes: Optional[str] = None,
    address: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    cost_per_kwh: Optional[float] = None,
) -> Optional[EVLocationLookup]:
    """Update a location row. Returns updated location or None if not found."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        return None
    loc.location_name = name
    loc.location_type = location_type
    loc.notes = notes
    loc.address = address
    loc.latitude = latitude
    loc.longitude = longitude
    loc.cost_per_kwh = cost_per_kwh
    await db.commit()
    await db.refresh(loc)
    return loc


async def delete_location(db: AsyncSession, location_id: int) -> bool:
    """Delete a location by id. Returns True if deleted, False if not found."""
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == location_id)
    )
    loc = result.scalar_one_or_none()
    if loc is None:
        return False
    await db.delete(loc)
    await db.commit()
    return True


async def get_app_setting(
    db: AsyncSession, key: str, default: str = ""
) -> str:
    """Return the value of an app_settings key, or default if not found."""
    result = await db.execute(
        select(AppSettings.value).where(AppSettings.key == key)
    )
    value = result.scalar_one_or_none()
    return value if value is not None else default


async def get_app_settings_dict(
    db: AsyncSession, keys: list[str]
) -> dict[str, str]:
    """Return a dict of key->value for the given app_settings keys.

    Missing keys default to empty string.
    """
    result = await db.execute(
        select(AppSettings.key, AppSettings.value).where(
            AppSettings.key.in_(keys)
        )
    )
    rows = result.all()
    found = {row.key: (row.value or "") for row in rows}
    # Fill missing keys with empty string
    return {k: found.get(k, "") for k in keys}


async def set_app_setting(db: AsyncSession, key: str, value: str) -> None:
    """Upsert a single key-value pair in app_settings."""
    stmt = pg_insert(AppSettings).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": value, "updated_at": stmt.excluded.updated_at},
    )
    await db.execute(stmt)
    await db.commit()


# ---------------------------------------------------------------------------
# Network charger templates (seed data)
# ---------------------------------------------------------------------------

NETWORK_CHARGER_TEMPLATES = {
    "Electrify America": [
        {"label": "150kW CCS", "charger_type": "DCFC", "rated_kw": 150, "voltage": 400, "amperage": 375, "connector_type": "CCS"},
        {"label": "350kW CCS", "charger_type": "DCFC", "rated_kw": 350, "voltage": 800, "amperage": 500, "connector_type": "CCS"},
    ],
    "Tesla Supercharger": [
        {"label": "250kW V3", "charger_type": "DCFC", "rated_kw": 250, "voltage": 400, "amperage": 625, "connector_type": "NACS"},
    ],
    "ChargePoint": [
        {"label": "L2 Charger", "charger_type": "L2", "rated_kw": 7.7, "voltage": 240, "amperage": 32, "connector_type": "J1772"},
        {"label": "62.5kW DCFC", "charger_type": "DCFC", "rated_kw": 62.5, "voltage": 400, "amperage": 156, "connector_type": "CCS"},
    ],
    "Home": [
        {"label": "L2 Wall Connector", "charger_type": "L2", "rated_kw": 9.6, "voltage": 240, "amperage": 40, "connector_type": "NACS"},
        {"label": "L1 Standard Outlet", "charger_type": "L1", "rated_kw": 1.4, "voltage": 120, "amperage": 12, "connector_type": "NACS"},
    ],
}


async def seed_charger_templates(db: AsyncSession) -> bool:
    """Seed network charger templates into app_settings as JSON.

    Idempotent -- skips if key already exists.
    Returns True if seeded, False if already existed.
    """
    existing = await get_app_setting(db, "network_charger_templates", "")
    if existing:
        return False
    await set_app_setting(db, "network_charger_templates", json.dumps(NETWORK_CHARGER_TEMPLATES))
    return True


async def get_charger_templates(db: AsyncSession) -> dict:
    """Load and parse network charger templates from app_settings.

    Returns empty dict if not found.
    """
    raw = await get_app_setting(db, "network_charger_templates", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Stall CRUD
# ---------------------------------------------------------------------------

async def get_stalls_for_location(db: AsyncSession, location_id: int) -> list[EVChargerStall]:
    """Return all stalls for a location ordered by stall_label."""
    result = await db.execute(
        select(EVChargerStall)
        .where(EVChargerStall.location_id == location_id)
        .order_by(EVChargerStall.stall_label)
    )
    return list(result.scalars().all())


async def create_stall(
    db: AsyncSession,
    location_id: int,
    label: str,
    charger_type: Optional[str] = None,
    rated_kw: Optional[float] = None,
    voltage: Optional[float] = None,
    amperage: Optional[float] = None,
    connector_type: Optional[str] = None,
    notes: Optional[str] = None,
    is_default: bool = False,
) -> EVChargerStall:
    """Create a new charger stall for a location."""
    stall = EVChargerStall(
        location_id=location_id,
        stall_label=label,
        charger_type=charger_type,
        rated_kw=rated_kw,
        voltage=voltage,
        amperage=amperage,
        connector_type=connector_type,
        notes=notes,
        is_default=is_default,
    )
    db.add(stall)
    await db.commit()
    await db.refresh(stall)
    return stall


async def update_stall(
    db: AsyncSession,
    stall_id: int,
    label: Optional[str] = None,
    charger_type: Optional[str] = None,
    rated_kw: Optional[float] = None,
    voltage: Optional[float] = None,
    amperage: Optional[float] = None,
    connector_type: Optional[str] = None,
    notes: Optional[str] = None,
    is_default: Optional[bool] = None,
) -> Optional[EVChargerStall]:
    """Update a charger stall. Returns updated stall or None if not found."""
    result = await db.execute(
        select(EVChargerStall).where(EVChargerStall.id == stall_id)
    )
    stall = result.scalar_one_or_none()
    if stall is None:
        return None
    if label is not None:
        stall.stall_label = label
    if charger_type is not None:
        stall.charger_type = charger_type
    if rated_kw is not None:
        stall.rated_kw = rated_kw
    if voltage is not None:
        stall.voltage = voltage
    if amperage is not None:
        stall.amperage = amperage
    if connector_type is not None:
        stall.connector_type = connector_type
    if notes is not None:
        stall.notes = notes
    if is_default is not None:
        stall.is_default = is_default
    await db.commit()
    await db.refresh(stall)
    return stall


async def delete_stall(db: AsyncSession, stall_id: int) -> bool:
    """Delete a charger stall by id. Returns True if deleted, False if not found."""
    result = await db.execute(
        select(EVChargerStall).where(EVChargerStall.id == stall_id)
    )
    stall = result.scalar_one_or_none()
    if stall is None:
        return False
    await db.delete(stall)
    await db.commit()
    return True
