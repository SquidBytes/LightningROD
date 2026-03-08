"""Location resolution service for EV charging sessions.

Finds or creates EVLocationLookup entries based on GPS coordinates,
address strings, and Home Assistant signals. Used by HA ingestion,
CSV import, and manual session creation.
"""

import math
import re
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings, EVLocationLookup
from web.queries.settings import resolve_network

# Maximum distance (meters) for geo-proximity matching
LOCATION_MATCH_RADIUS_M = 100

# Earth radius in meters for Haversine
_EARTH_RADIUS_M = 6_371_000

# Address abbreviation expansions (applied as whole-word replacements)
_ADDRESS_ABBREVIATIONS = {
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "rd": "road",
    "ln": "lane",
    "ct": "court",
    "pl": "place",
    "cir": "circle",
    "pkwy": "parkway",
    "hwy": "highway",
}


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in meters between two GPS points.

    Uses the standard Haversine formula with the math stdlib.
    """
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_M * c


def normalize_address(addr: Optional[str]) -> Optional[str]:
    """Normalize an address string for comparison.

    Lowercases, collapses whitespace, expands common abbreviations.
    Returns None if input is None or empty/whitespace.
    """
    if not addr or not addr.strip():
        return None

    result = addr.lower().strip()
    # Collapse multiple whitespace to single space
    result = re.sub(r"\s+", " ", result)

    # Expand abbreviations as whole words
    words = result.split()
    expanded = []
    for word in words:
        # Strip trailing punctuation for matching, preserve it
        clean = word.rstrip(".,;:")
        suffix = word[len(clean):]
        expanded.append(_ADDRESS_ABBREVIATIONS.get(clean, clean) + suffix)
    result = " ".join(expanded)

    return result


def _infer_location_type(
    location_data: dict, network_name: Optional[str]
) -> str:
    """Infer location type from HA signals.

    Returns "home" if location name is "SAVED" (or empty), id is "0",
    and network is "UNKNOWN" or absent. Returns "public" otherwise.
    """
    name = str(location_data.get("location_name", "") or "").strip().upper()
    loc_id = str(location_data.get("location_id", "") or "").strip()
    net = (network_name or "").strip().upper()

    is_saved_or_empty = name in ("SAVED", "")
    is_id_zero = loc_id == "0"
    is_unknown_network = net in ("UNKNOWN", "")

    if is_saved_or_empty and is_id_zero and is_unknown_network:
        return "home"
    return "public"


def _find_geo_match(
    locations: list, latitude: float, longitude: float
) -> Optional[object]:
    """Find the first location within LOCATION_MATCH_RADIUS_M of the given coordinates."""
    for loc in locations:
        if loc.latitude is not None and loc.longitude is not None:
            dist = haversine_meters(
                float(loc.latitude), float(loc.longitude),
                latitude, longitude,
            )
            if dist <= LOCATION_MATCH_RADIUS_M:
                return loc
    return None


def _find_address_match(
    locations: list, address: str
) -> Optional[object]:
    """Find the first location whose normalized address matches."""
    norm_incoming = normalize_address(address)
    if not norm_incoming:
        return None
    for loc in locations:
        if loc.address and normalize_address(loc.address) == norm_incoming:
            return loc
    return None


async def resolve_location(
    db: AsyncSession,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    address: Optional[str] = None,
    network_id: Optional[int] = None,
    network_name: Optional[str] = None,
    location_name: Optional[str] = None,
    location_type: Optional[str] = None,
    address_dict: Optional[dict] = None,
    source_system: str = "home_assistant",
    _location_data: Optional[dict] = None,
    _network_name_raw: Optional[str] = None,
) -> Optional[int]:
    """Resolve an incoming location to an EVLocationLookup ID.

    Resolution priority:
    1. Geo-proximity match (within 100m) if lat/lon provided
    2. Normalized address string match as fallback
    3. Auto-create new unverified location if no match

    Parameters:
        db: async database session
        latitude/longitude: GPS coordinates
        address: street address string
        network_id: explicit network ID (takes priority)
        network_name: network name for lookup/auto-create
        location_name: name for the location
        location_type: explicit type override
        address_dict: dict with city/state/country from HA
        source_system: origin identifier (default "home_assistant")
        _location_data: raw HA location dict for home detection
        _network_name_raw: raw network name from HA for home detection
    """
    if latitude is None and longitude is None and not address:
        return None

    # Load all locations for matching
    result = await db.execute(select(EVLocationLookup))
    all_locations = list(result.scalars().all())

    matched = None

    # 1. Geo-proximity match
    if latitude is not None and longitude is not None:
        matched = _find_geo_match(all_locations, latitude, longitude)

    # 2. Address fallback
    if matched is None and address:
        matched = _find_address_match(all_locations, address)

    # 3. Handle match
    if matched is not None:
        # Protect user-edited locations
        if matched.source_system == "manual":
            return matched.id

        # Enrich: fill NULL fields without overwriting existing values
        if address and matched.address is None:
            matched.address = address
        if latitude is not None and matched.latitude is None:
            matched.latitude = latitude
        if longitude is not None and matched.longitude is None:
            matched.longitude = longitude
        if location_name and matched.location_name is None:
            matched.location_name = location_name

        await db.flush()
        return matched.id

    # 4. No match -- create new location

    # Determine network_id for new location
    resolved_network_id = network_id
    if not resolved_network_id and network_name:
        if network_name.strip().upper() == "UNKNOWN":
            resolved_network_id = None
        else:
            resolved_network_id = await resolve_network(db, network_name=network_name)

    # Determine location type
    resolved_type = location_type
    if not resolved_type and _location_data:
        resolved_type = _infer_location_type(_location_data, _network_name_raw)

    # Home detection via AppSettings
    if resolved_type == "home":
        home_lat = await _get_setting(db, "home_latitude")
        home_lon = await _get_setting(db, "home_longitude")
        home_name = await _get_setting(db, "home_location_name")

        if home_lat and home_lon:
            h_lat, h_lon = float(home_lat), float(home_lon)
            # Check if any existing location matches home coords
            home_match = _find_geo_match(all_locations, h_lat, h_lon)
            if home_match:
                return home_match.id

            # Create at home coordinates
            new_loc = EVLocationLookup(
                location_name=home_name or "Home",
                latitude=h_lat,
                longitude=h_lon,
                address=address,
                location_type="home",
                network_id=None,
                is_verified=False,
                source_system=source_system,
            )
            db.add(new_loc)
            await db.flush()
            return new_loc.id

    # Auto-create location name
    resolved_name = location_name
    if not resolved_name and address_dict:
        resolved_name = address_dict.get("city")
    if not resolved_name and address:
        resolved_name = address
    if not resolved_name:
        resolved_name = "Unknown Location"

    new_loc = EVLocationLookup(
        location_name=resolved_name,
        latitude=latitude,
        longitude=longitude,
        address=address,
        location_type=resolved_type or "public",
        network_id=resolved_network_id,
        is_verified=False,
        source_system=source_system,
    )
    db.add(new_loc)
    await db.flush()
    return new_loc.id


async def _get_setting(db: AsyncSession, key: str) -> Optional[str]:
    """Get a single app setting value."""
    result = await db.execute(
        select(AppSettings.value).where(AppSettings.key == key)
    )
    return result.scalar_one_or_none()
