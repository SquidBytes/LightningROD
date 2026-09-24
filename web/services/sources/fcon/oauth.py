"""Consent flow: start it, check what comes back, and explain Ford's failures."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from web.services.sources.fcon.client import FordError

# Ford keeps only the first 16 characters of `state`.
STATE_BYTES = 8
# The authorization code and Ford's state JWT lived ~300 s in every capture.
CODE_LIFETIME = timedelta(minutes=5)
CALLBACK_PATH = "/auth/ford/callback"
_FORD_SIGN_IN_HOSTS = ("ford.com", "b2clogin.com", "onmicrosoft.com")
_WRAPPERS = "\"'<>` "


class PasteRejected(ValueError):
    """The pasted value cannot be exchanged; the message says why."""


def new_state() -> str:
    return secrets.token_hex(STATE_BYTES)


def redirect_uri_for(host: str | None, port: int | None, fallback_port: int) -> tuple[str, bool]:
    """(redirect URI to register, whether it is a guess the user should confirm).

    Ford accepts `localhost` or a public host only, so the host is always
    localhost; the port is the one the browser used to reach this page.
    """
    if port is None:
        # Behind a proxy on 80/443 the request carries no usable port.
        return f"http://localhost:{fallback_port}{CALLBACK_PATH}", True
    return f"http://localhost:{port}{CALLBACK_PATH}", False


@dataclass(frozen=True)
class ReturnedCode:
    code: str
    state: str | None


def _query_of(text: str) -> tuple[dict[str, str], str | None] | None:
    """(query params, host) for a URL, a `?query`, or a bare `code=` value."""
    if "://" in text:
        parts = urlsplit(text)
        if not parts.netloc:
            return None
        return _flatten(parse_qs(parts.query)), (parts.hostname or "").lower()
    candidate = text.lstrip("?&")
    if "code=" in candidate or "error=" in candidate:
        return _flatten(parse_qs(candidate)), None
    return None


def _flatten(query: dict[str, list[str]]) -> dict[str, str]:
    return {k: v[0] for k, v in query.items() if v}


def _is_ford_host(host: str | None) -> bool:
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in _FORD_SIGN_IN_HOSTS)


def parse_returned(
    pasted: str,
    *,
    expected_state: str | None,
    state_created_at: datetime | None,
    now: datetime | None = None,
) -> ReturnedCode:
    """Validate what the user pasted (or the callback received), in plan order."""
    text = pasted.strip().strip(_WRAPPERS)
    parsed = _query_of(text)
    if parsed is None:
        raise PasteRejected(
            "That doesn't look like a URL. Copy the whole address bar, "
            "starting with http://."
        )
    query, host = parsed
    if "error" in query:
        detail = query.get("error_description") or query["error"]
        raise PasteRejected(f"Ford declined the request: {detail}")
    code = query.get("code")
    if not code:
        if _is_ford_host(host):
            raise PasteRejected(
                "That's the sign-in link, not the address you landed on after approving."
            )
        raise PasteRejected(
            "That address has no code in it. Copy the address you landed on "
            "after approving the vehicle."
        )
    state = query.get("state")
    if state is not None and (expected_state is None or state != expected_state):
        raise PasteRejected(
            "This doesn't match the connection LightningROD started. "
            "Click Connect Vehicle and try again."
        )
    if state_created_at is None:
        raise PasteRejected("No connection is in progress. Click Connect Vehicle first.")
    now = now or datetime.now(UTC)
    if now - state_created_at > CODE_LIFETIME:
        raise PasteRejected(
            "That code expired — they're only valid about five minutes. "
            "Click Connect Vehicle to start again."
        )
    return ReturnedCode(code=code, state=state)


def explain_token_error(err: FordError) -> str:
    """Plain-English reason a token exchange failed."""
    body = (err.body or "").lower()
    if err.status == 404 and "credential" in body:
        return (
            "Ford doesn't recognise your Client ID yet. New credentials take a "
            "couple of hours to activate — try again later."
        )
    if "invalid_grant" in body or "aadb2c90090" in body:
        return (
            "That code was already used or is malformed. Click Connect Vehicle "
            "for a fresh one."
        )
    if "invalid_client" in body:
        return "Your Client Secret doesn't match that Client ID. Re-check both in Settings."
    if err.status is None:
        return "Couldn't reach Ford. Check this server's internet connection and try again."
    return f"Ford rejected the request ({err.status}). Click Connect Vehicle to try again."
