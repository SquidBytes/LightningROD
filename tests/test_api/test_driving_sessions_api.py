"""API integration tests for renamed /driving/sessions route. phase_25 Wave 1."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_driving_sessions_returns_200(client):
    """GET /driving/sessions returns 200 (successor to /trips)."""
    response = await client.get("/driving/sessions")
    assert response.status_code == 200
    assert "Trip History" in response.text


async def test_phase_25_old_trips_url_returns_404(client):
    """GET /trips returns 404 (clean break, no redirect)."""
    response = await client.get("/trips")
    assert response.status_code == 404


async def test_phase_25_driving_sessions_no_efficiency_trend_chart(client):
    """Response body does NOT contain an 'Efficiency Trend' or 'Driving Efficiency' card."""
    response = await client.get("/driving/sessions")
    assert response.status_code == 200
    assert "Efficiency Trend" not in response.text
    # 'Driving Efficiency' card is on /driving/performance, not here.
    # Note: the page subtitle says "Track driving efficiency…" (lowercase) — we
    # only want to catch the capitalized card title.
    assert "Driving Efficiency" not in response.text


async def test_phase_25_driving_sessions_htmx_range_filter(client):
    """GET /driving/sessions?range=7d with HX-Request returns the partial."""
    response = await client.get(
        "/driving/sessions?range=7d",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Partial omits the full page <h1>; summary card title is present.
    assert "Trip History" not in response.text
    assert "Total Trips" in response.text


async def test_delete_trip(client, db_session):
    """DELETE /driving/sessions/{id} removes the trip and fires trip-deleted."""
    from sqlalchemy import select

    from db.models.trip_metrics import EVTripMetrics
    from tests.factories.trips import TripFactory
    from tests.factories.vehicles import VehicleFactory

    vehicle = await VehicleFactory.create(db_session)
    trip = await TripFactory.create(db_session, device_id=vehicle.device_id)

    response = await client.delete(f"/driving/sessions/{trip.id}")
    assert response.status_code == 200
    assert response.headers.get("hx-trigger") == "trip-deleted"

    remaining = (
        await db_session.execute(
            select(EVTripMetrics).where(EVTripMetrics.id == trip.id)
        )
    ).scalar_one_or_none()
    assert remaining is None


async def test_delete_trip_missing_returns_404(client):
    response = await client.delete("/driving/sessions/999999")
    assert response.status_code == 404


async def test_create_manual_trip_stores_duration_in_seconds(client, db_session):
    """POST /driving/sessions converts the minutes form field to canonical seconds."""
    from sqlalchemy import select

    from db.models.trip_metrics import EVTripMetrics
    from tests.factories.vehicles import VehicleFactory

    await VehicleFactory.create(db_session)
    response = await client.post(
        "/driving/sessions",
        data={
            "trip_date": "2026-07-01",
            "distance": "10",
            "duration_minutes": "45",
            "energy_consumed": "3.5",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200

    trip = (
        await db_session.execute(
            select(EVTripMetrics).order_by(EVTripMetrics.id.desc()).limit(1)
        )
    ).scalar_one()
    assert float(trip.duration) == 45 * 60


async def test_trip_sessions_accepts_custom_date_window(client, db_session):
    """GET /driving/sessions honors date_from/date_to instead of snapping to the preset."""
    from datetime import UTC, datetime

    from tests.factories.trips import TripFactory
    from tests.factories.vehicles import VehicleFactory

    vehicle = await VehicleFactory.create(db_session)
    await TripFactory.create(
        db_session, device_id=vehicle.device_id,
        end_time=datetime(2026, 3, 15, 12, 0, tzinfo=UTC), distance=42.0,
    )
    await TripFactory.create(
        db_session, device_id=vehicle.device_id,
        end_time=datetime(2026, 5, 20, 12, 0, tzinfo=UTC), distance=99.0,
    )

    response = await client.get(
        "/driving/sessions?range=&date_from=2026-03-01&date_to=2026-03-31"
    )
    assert response.status_code == 200
    # Summary card renders the trip count as a bare integer paragraph --
    # only the March trip is in the window.
    assert '<p class="text-3xl font-bold">1</p>' in response.text
