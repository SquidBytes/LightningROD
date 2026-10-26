"""Paste validation, in the plan's order, and Ford error translation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from web.services.sources.fcon.client import FordError, authorize_url
from web.services.sources.fcon.oauth import (
    PasteRejected,
    explain_token_error,
    new_state,
    parse_returned,
    redirect_uri_for,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
STATE = "a3f9c2e81b4d7e02"
CB = "http://localhost:8000/auth/ford/callback"


def _parse(text: str, *, state=STATE, age=timedelta(minutes=1)):
    return parse_returned(
        text, expected_state=state, state_created_at=NOW - age, now=NOW
    )


def test_state_is_16_hex():
    s = new_state()
    assert len(s) == 16
    int(s, 16)


def test_full_url_accepted():
    got = _parse(f"{CB}?code=abc.def&state={STATE}")
    assert got.code == "abc.def"


@pytest.mark.parametrize("wrap", ['"{}"', "<{}>", "  {}\n", "'{}'"])
def test_wrappers_and_whitespace_stripped(wrap):
    assert _parse(wrap.format(f"{CB}?code=xyz&state={STATE}")).code == "xyz"


def test_bare_code_accepted():
    assert _parse("code=justthecode").code == "justthecode"


def test_not_a_url():
    with pytest.raises(PasteRejected, match="doesn't look like a URL"):
        _parse("hello there")


def test_ford_error_shown_verbatim():
    with pytest.raises(PasteRejected, match="Ford declined the request: User cancelled"):
        _parse(f"{CB}?error=access_denied&error_description=User+cancelled")


def test_sign_in_link_detected():
    link = authorize_url("cid", CB, STATE)
    with pytest.raises(PasteRejected, match="sign-in link"):
        _parse(link)


def test_other_url_without_code():
    with pytest.raises(PasteRejected, match="no code"):
        _parse("http://localhost:8000/settings")


def test_state_mismatch():
    with pytest.raises(PasteRejected, match="doesn't match"):
        _parse(f"{CB}?code=x&state=0000000000000000")


def test_state_without_pending_flow():
    with pytest.raises(PasteRejected, match="doesn't match"):
        _parse(f"{CB}?code=x&state={STATE}", state=None)


def test_expired_after_five_minutes():
    with pytest.raises(PasteRejected, match="expired"):
        _parse(f"{CB}?code=x&state={STATE}", age=timedelta(minutes=6))


def test_redirect_uri_from_request_port():
    assert redirect_uri_for("tower.local", 8123, 8000) == (
        "http://localhost:8123/auth/ford/callback",
        False,
    )


def test_redirect_uri_falls_back_when_port_unknown():
    uri, guessed = redirect_uri_for("lr.example", None, 8000)
    assert uri == "http://localhost:8000/auth/ford/callback"
    assert guessed


@pytest.mark.parametrize(
    ("status", "body", "needle"),
    [
        (404, '{"error":"credential not found"}', "doesn't recognise your Client ID"),
        (400, '{"error":"invalid_grant"}', "already used"),
        (400, "AADB2C90090: bad code", "already used"),
        (401, '{"error":"invalid_client"}', "Client Secret doesn't match"),
        (None, "", "Couldn't reach Ford"),
        (500, "boom", "(500)"),
    ],
)
def test_token_errors_translated(status, body, needle):
    assert needle in explain_token_error(FordError("x", status=status, body=body))
