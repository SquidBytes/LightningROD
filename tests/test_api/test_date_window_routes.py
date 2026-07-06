"""Every filter-bar page accepts a custom date_from/date_to window."""

import pytest

pytestmark = pytest.mark.db

_WINDOW = "range=&date_from=2026-03-01&date_to=2026-03-31"


@pytest.mark.parametrize(
    "path",
    [
        "/charging/sessions",
        "/charging/costs",
        "/charging/performance",
        "/battery",
        "/driving/sessions",
        "/driving/performance",
    ],
)
async def test_page_accepts_custom_date_window(client, path):
    response = await client.get(f"{path}?{_WINDOW}")
    assert response.status_code == 200
