import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings, EVChargerStall, EVChargingNetwork, EVLocationLookup, EVNetworkSubscription

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

# Derived lookups from predefined list — used when auto-creating known networks
NETWORK_COLORS = {n["name"]: n["color"] for n in PREDEFINED_NETWORKS}
_PREDEFINED_BY_NAME = {n["name"].lower(): n for n in PREDEFINED_NETWORKS}
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

    # Auto-create new network — use predefined data if it's a known network
    known = _PREDEFINED_BY_NAME.get(name.lower())
    new_net = EVChargingNetwork(
        network_name=known["name"] if known else name,
        is_free=known["is_free"] if known else False,
        color=known["color"] if known else DEFAULT_COLOR,
        cost_per_kwh=known["cost_per_kwh"] if known else None,
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

    If color/cost not provided, auto-fill from predefined network data if
    the name matches a known network, falling back to DEFAULT_COLOR.
    """
    known = _PREDEFINED_BY_NAME.get(name.lower().strip()) if name else None
    resolved_color = color if color else (known["color"] if known else DEFAULT_COLOR)
    resolved_cost = cost_per_kwh if cost_per_kwh is not None else (known["cost_per_kwh"] if known else None)
    network = EVChargingNetwork(
        network_name=name,
        cost_per_kwh=resolved_cost,
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
    known = _PREDEFINED_BY_NAME.get(name.lower().strip()) if name else None
    network.network_name = name
    network.cost_per_kwh = cost_per_kwh
    network.is_free = is_free
    network.color = color if color else (known["color"] if known else DEFAULT_COLOR)
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


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


async def get_subscriptions_for_network(
    db: AsyncSession, network_id: int
) -> list[EVNetworkSubscription]:
    """Return all subscription periods for a network, ordered by start_date desc."""
    result = await db.execute(
        select(EVNetworkSubscription)
        .where(EVNetworkSubscription.network_id == network_id)
        .order_by(EVNetworkSubscription.start_date.desc())
    )
    return list(result.scalars().all())


async def get_all_subscriptions_by_network(
    db: AsyncSession,
) -> dict[int, list[EVNetworkSubscription]]:
    """Return dict mapping network_id to list of EVNetworkSubscription objects.

    Used by cost summary to batch-load all subscriptions.
    """
    result = await db.execute(
        select(EVNetworkSubscription).order_by(EVNetworkSubscription.start_date)
    )
    all_subs = result.scalars().all()
    by_network: dict[int, list[EVNetworkSubscription]] = {}
    for sub in all_subs:
        by_network.setdefault(sub.network_id, []).append(sub)
    return by_network


async def validate_no_overlap(
    db: AsyncSession,
    network_id: int,
    start_date,
    end_date=None,
    exclude_id: int = None,
) -> bool:
    """Check that a new/edited period does not overlap any existing period for the same network.

    Treat null end_date as date.max. Return True if no overlap.
    """
    from datetime import date as date_type

    stmt = select(EVNetworkSubscription).where(
        EVNetworkSubscription.network_id == network_id
    )
    if exclude_id:
        stmt = stmt.where(EVNetworkSubscription.id != exclude_id)

    result = await db.execute(stmt)
    existing = result.scalars().all()

    for period in existing:
        # Two ranges overlap if: start1 <= end2 AND start2 <= end1
        p_end = period.end_date or date_type.max
        new_end = end_date or date_type.max
        if start_date <= p_end and period.start_date <= new_end:
            return False  # overlap detected
    return True


async def create_subscription(
    db: AsyncSession,
    network_id: int,
    member_rate: float,
    monthly_fee: float,
    start_date,
    end_date=None,
    notes: str = None,
) -> EVNetworkSubscription:
    """Create a new subscription period. Validates no overlap first.

    Raises ValueError if overlap detected.
    """
    if not await validate_no_overlap(db, network_id, start_date, end_date):
        raise ValueError("Subscription period overlaps with an existing period for this network")

    sub = EVNetworkSubscription(
        network_id=network_id,
        member_rate=member_rate,
        monthly_fee=monthly_fee,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def update_subscription(
    db: AsyncSession,
    subscription_id: int,
    member_rate: float,
    monthly_fee: float,
    start_date,
    end_date=None,
    notes: str = None,
) -> Optional[EVNetworkSubscription]:
    """Update an existing subscription period. Validates no overlap (excluding self).

    Returns updated row or None if not found. Raises ValueError if overlap detected.
    """
    result = await db.execute(
        select(EVNetworkSubscription).where(EVNetworkSubscription.id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return None

    if not await validate_no_overlap(db, sub.network_id, start_date, end_date, exclude_id=subscription_id):
        raise ValueError("Subscription period overlaps with an existing period for this network")

    sub.member_rate = member_rate
    sub.monthly_fee = monthly_fee
    sub.start_date = start_date
    sub.end_date = end_date
    sub.notes = notes
    await db.commit()
    await db.refresh(sub)
    return sub


async def delete_subscription(db: AsyncSession, subscription_id: int) -> bool:
    """Delete a subscription period. Returns True if deleted, False if not found."""
    result = await db.execute(
        select(EVNetworkSubscription).where(EVNetworkSubscription.id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return False
    await db.delete(sub)
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
