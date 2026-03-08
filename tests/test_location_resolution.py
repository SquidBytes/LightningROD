"""Tests for location resolution service (web/queries/locations.py)."""

import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Pure function tests (no DB needed)
# ---------------------------------------------------------------------------


class TestHaversineMeters:
    """Tests for haversine_meters distance calculation."""

    def test_same_point_returns_zero(self):
        from web.queries.locations import haversine_meters

        assert haversine_meters(0, 0, 0, 0) == 0

    def test_one_degree_latitude_approx_111km(self):
        from web.queries.locations import haversine_meters

        dist = haversine_meters(0, 0, 1, 0)
        # ~111,195 meters for 1 degree at equator
        assert 110_000 < dist < 112_000

    def test_nearby_points_within_100m(self):
        """Two points ~50m apart should return distance < 100."""
        from web.queries.locations import haversine_meters

        # ~50m apart at mid-latitudes
        lat1, lon1 = 40.0, -74.0
        lat2, lon2 = 40.00045, -74.0  # ~50m north
        dist = haversine_meters(lat1, lon1, lat2, lon2)
        assert dist < 100

    def test_distant_points_over_threshold(self):
        """Two points 1km apart should be > 100m."""
        from web.queries.locations import haversine_meters

        lat1, lon1 = 40.0, -74.0
        lat2, lon2 = 40.009, -74.0  # ~1km north
        dist = haversine_meters(lat1, lon1, lat2, lon2)
        assert dist > 100


class TestNormalizeAddress:
    """Tests for normalize_address string normalization."""

    def test_lowercases_and_collapses_whitespace(self):
        from web.queries.locations import normalize_address

        result = normalize_address("123  Main   ST")
        assert result == "123 main street"

    def test_normalizes_abbreviations(self):
        from web.queries.locations import normalize_address

        assert "avenue" in normalize_address("5th Ave")
        assert "boulevard" in normalize_address("Sunset Blvd")
        assert "drive" in normalize_address("Oak Dr")
        assert "road" in normalize_address("Elm Rd")

    def test_none_returns_none(self):
        from web.queries.locations import normalize_address

        assert normalize_address(None) is None

    def test_empty_string_returns_none(self):
        from web.queries.locations import normalize_address

        assert normalize_address("") is None

    def test_whitespace_only_returns_none(self):
        from web.queries.locations import normalize_address

        assert normalize_address("   ") is None


class TestInferLocationType:
    """Tests for _infer_location_type."""

    def test_home_detection_saved_id0_unknown(self):
        from web.queries.locations import _infer_location_type

        data = {"location_name": "SAVED", "location_id": "0"}
        result = _infer_location_type(data, "UNKNOWN")
        assert result == "home"

    def test_home_detection_empty_name_id0_no_network(self):
        from web.queries.locations import _infer_location_type

        data = {"location_name": "", "location_id": "0"}
        result = _infer_location_type(data, None)
        assert result == "home"

    def test_public_for_normal_location(self):
        from web.queries.locations import _infer_location_type

        data = {"location_name": "Walmart", "location_id": "123"}
        result = _infer_location_type(data, "ChargePoint")
        assert result == "public"

    def test_public_when_name_is_saved_but_has_network(self):
        from web.queries.locations import _infer_location_type

        data = {"location_name": "SAVED", "location_id": "0"}
        result = _infer_location_type(data, "ChargePoint")
        assert result == "public"


# ---------------------------------------------------------------------------
# Database-dependent tests (mocked)
# ---------------------------------------------------------------------------


def _make_mock_location(**kwargs):
    """Create a mock EVLocationLookup with given attrs."""
    loc = MagicMock()
    loc.id = kwargs.get("id", 1)
    loc.location_name = kwargs.get("location_name", "Test Location")
    loc.address = kwargs.get("address", None)
    loc.latitude = kwargs.get("latitude", None)
    loc.longitude = kwargs.get("longitude", None)
    loc.location_type = kwargs.get("location_type", None)
    loc.network_id = kwargs.get("network_id", None)
    loc.cost_per_kwh = kwargs.get("cost_per_kwh", None)
    loc.notes = kwargs.get("notes", None)
    loc.is_verified = kwargs.get("is_verified", True)
    loc.source_system = kwargs.get("source_system", None)
    return loc


def _make_mock_db(locations=None, settings=None):
    """Create a mock AsyncSession that returns locations from queries.

    locations: list of mock location objects to return for EVLocationLookup queries.
    settings: dict of key->value for AppSettings queries.
    """
    mock_db = AsyncMock()
    settings = settings or {}

    async def mock_execute(stmt):
        result = MagicMock()
        # Detect what's being queried by inspecting the statement
        stmt_str = str(stmt)

        if "ev_location_lookup" in stmt_str.lower() or "EVLocationLookup" in str(type(stmt)):
            # Return locations
            scalars_mock = MagicMock()
            scalars_mock.all.return_value = locations or []
            result.scalars.return_value = scalars_mock
        elif "app_settings" in stmt_str.lower():
            # Return a setting value
            scalar_val = None
            for key, val in settings.items():
                if key in stmt_str:
                    scalar_val = val
                    break
            result.scalar_one_or_none.return_value = scalar_val
        else:
            scalars_mock = MagicMock()
            scalars_mock.all.return_value = locations or []
            result.scalars.return_value = scalars_mock

        return result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    return mock_db


@pytest.mark.asyncio
async def test_resolve_location_geo_match_returns_existing_id():
    """resolve_location returns existing location_id when geo-match within 100m."""
    from web.queries.locations import resolve_location

    existing = _make_mock_location(
        id=42, latitude=40.0, longitude=-74.0, source_system="home_assistant"
    )
    mock_db = _make_mock_db(locations=[existing])

    result = await resolve_location(
        mock_db, latitude=40.00001, longitude=-74.00001
    )
    assert result == 42


@pytest.mark.asyncio
async def test_resolve_location_address_fallback_returns_existing_id():
    """resolve_location returns existing location_id when address matches (fallback)."""
    from web.queries.locations import resolve_location

    existing = _make_mock_location(
        id=55, address="123 Main Street", latitude=None, longitude=None,
        source_system="home_assistant"
    )
    mock_db = _make_mock_db(locations=[existing])

    result = await resolve_location(
        mock_db, address="123  Main  St"
    )
    assert result == 55


@pytest.mark.asyncio
async def test_resolve_location_auto_creates_when_no_match():
    """resolve_location auto-creates new unverified location when no match."""
    from web.queries.locations import resolve_location

    mock_db = _make_mock_db(locations=[])

    result = await resolve_location(
        mock_db,
        latitude=35.0, longitude=-80.0,
        address="456 Oak Drive",
        location_name="Test Charger",
    )

    # Should have called db.add() with a new location
    assert mock_db.add.called
    new_loc = mock_db.add.call_args[0][0]
    assert new_loc.is_verified is False
    assert new_loc.source_system == "home_assistant"
    assert new_loc.location_name == "Test Charger"


@pytest.mark.asyncio
async def test_resolve_location_skips_enrichment_on_manual():
    """resolve_location skips enrichment on source_system='manual' locations."""
    from web.queries.locations import resolve_location

    existing = _make_mock_location(
        id=10, latitude=40.0, longitude=-74.0,
        source_system="manual", address=None
    )
    mock_db = _make_mock_db(locations=[existing])

    result = await resolve_location(
        mock_db, latitude=40.00001, longitude=-74.00001,
        address="New Address"
    )
    # Should return id without modifying
    assert result == 10
    # address should NOT have been changed
    assert existing.address is None


@pytest.mark.asyncio
async def test_resolve_location_enriches_null_fields():
    """resolve_location fills NULL fields on HA-created locations (enrichment)."""
    from web.queries.locations import resolve_location

    existing = _make_mock_location(
        id=20, latitude=40.0, longitude=-74.0,
        source_system="home_assistant", address=None, location_name="Test"
    )
    mock_db = _make_mock_db(locations=[existing])

    result = await resolve_location(
        mock_db, latitude=40.00001, longitude=-74.00001,
        address="789 Elm Road"
    )
    assert result == 20
    # address should now be filled
    assert existing.address == "789 Elm Road"


@pytest.mark.asyncio
async def test_resolve_location_ignores_unknown_network():
    """resolve_location ignores UNKNOWN network (sets network_id=NULL)."""
    from web.queries.locations import resolve_location

    mock_db = _make_mock_db(locations=[])

    result = await resolve_location(
        mock_db,
        latitude=35.0, longitude=-80.0,
        network_name="UNKNOWN",
        location_name="Some Place",
    )

    assert mock_db.add.called
    new_loc = mock_db.add.call_args[0][0]
    assert new_loc.network_id is None


@pytest.mark.asyncio
async def test_resolve_location_auto_creates_network():
    """resolve_location auto-creates network via resolve_network when network_name provided."""
    from web.queries.locations import resolve_location

    mock_db = _make_mock_db(locations=[])

    with patch("web.queries.locations.resolve_network", new_callable=AsyncMock) as mock_rn:
        mock_rn.return_value = 99

        result = await resolve_location(
            mock_db,
            latitude=35.0, longitude=-80.0,
            network_name="ChargePoint",
            location_name="Mall Charger",
        )

        mock_rn.assert_called_once_with(mock_db, network_name="ChargePoint")
        new_loc = mock_db.add.call_args[0][0]
        assert new_loc.network_id == 99


@pytest.mark.asyncio
async def test_resolve_location_home_detection_with_settings():
    """resolve_location uses AppSettings home_latitude/home_longitude for home detection."""
    from web.queries.locations import resolve_location

    mock_db = _make_mock_db(
        locations=[],
        settings={
            "home_latitude": "40.0",
            "home_longitude": "-74.0",
        }
    )

    # Simulate home signals
    result = await resolve_location(
        mock_db,
        latitude=40.00001, longitude=-74.00001,
        location_name="SAVED",
        location_type="home",
        source_system="home_assistant",
        _location_data={"location_name": "SAVED", "location_id": "0"},
        _network_name_raw="UNKNOWN",
    )

    # Should create a location at home coordinates
    assert mock_db.add.called
    new_loc = mock_db.add.call_args[0][0]
    assert new_loc.location_type == "home"
