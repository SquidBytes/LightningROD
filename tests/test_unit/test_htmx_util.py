"""error_response builds a toast-bound fragment."""

import pytest

from web.util.htmx import error_response

pytestmark = pytest.mark.unit


def test_defaults_to_400_and_targets_the_toast_root():
    response = error_response("Nope.")
    assert response.status_code == 400
    assert response.headers["HX-Retarget"] == "#toast-root"
    assert response.headers["HX-Reswap"] == "beforeend"
    assert b"Nope." in response.body


def test_keeps_the_given_status():
    assert error_response("Taken.", 409).status_code == 409


def test_escapes_markup_in_the_message():
    body = bytes(error_response("<script>alert(1)</script>").body)
    assert b"<script>" not in body
    assert b"&lt;script&gt;" in body
