"""Unit tests for DemoModeMiddleware."""
import json

import pytest

from web.middleware.demo_mode import BLOCKED_METHODS, DemoModeMiddleware


class _StubApp:
    """Minimal ASGI app that records whether it was reached."""
    def __init__(self):
        self.called = False
        self.last_scope = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.last_scope = scope
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"OK"})


class _Capture:
    def __init__(self):
        self.events = []

    async def receive(self):
        return {"type": "http.request", "body": b""}

    async def send(self, msg):
        self.events.append(msg)


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(BLOCKED_METHODS))
async def test_blocks_unsafe_methods(method):
    stub = _StubApp()
    mw = DemoModeMiddleware(stub)
    cap = _Capture()
    scope = {"type": "http", "method": method, "path": "/"}
    await mw(scope, cap.receive, cap.send)
    assert not stub.called, f"{method} should not reach downstream"
    start = next(e for e in cap.events if e["type"] == "http.response.start")
    body = next(e for e in cap.events if e["type"] == "http.response.body")
    assert start["status"] == 403
    payload = json.loads(body["body"])
    assert "Demo mode" in payload["detail"]
    assert "Self-host" in payload["detail"]


@pytest.mark.unit
@pytest.mark.parametrize("method", ["GET", "POST", "HEAD", "OPTIONS"])
async def test_safe_methods_pass_through(method):
    stub = _StubApp()
    mw = DemoModeMiddleware(stub)
    cap = _Capture()
    scope = {"type": "http", "method": method, "path": "/"}
    await mw(scope, cap.receive, cap.send)
    assert stub.called, f"{method} should reach downstream"


@pytest.mark.unit
async def test_non_http_scope_passes_through():
    stub = _StubApp()
    mw = DemoModeMiddleware(stub)
    cap = _Capture()
    scope = {"type": "websocket", "path": "/ws"}
    await mw(scope, cap.receive, cap.send)
    assert stub.called, "websocket scope should not be blocked"


@pytest.mark.unit
def test_blocked_methods_set_unchanged():
    """Lock the blocked-method contract — modifying this set is intentional only."""
    assert BLOCKED_METHODS == frozenset({"DELETE", "PUT", "PATCH"})
