"""Error replies reach htmx as toast fragments with real status codes."""

import pytest

pytestmark = [pytest.mark.api, pytest.mark.db]

TOAST_ROOT = "#toast-root"


async def test_route_error_is_a_toast_fragment(client):
    response = await client.post("/settings/ice-vehicles", data={"label": " "})
    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == TOAST_ROOT
    assert "Label is required" in response.text


async def test_http_exception_is_a_toast_for_htmx(client):
    response = await client.post(
        "/settings/data-repair/not-a-repair/preview", headers={"HX-Request": "true"}
    )
    assert response.status_code == 404
    assert response.headers["HX-Retarget"] == TOAST_ROOT
    assert "Unknown repair: not-a-repair" in response.text


async def test_http_exception_stays_json_without_htmx(client):
    response = await client.post("/settings/data-repair/not-a-repair/preview")
    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown repair: not-a-repair"}
