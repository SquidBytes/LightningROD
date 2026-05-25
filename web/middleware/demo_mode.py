"""Demo-mode ASGI middleware: blocks unsafe HTTP methods with 403 JSON."""
import json
from collections.abc import Awaitable, Callable

ASGIScope = dict
ASGIReceive = Callable[[], Awaitable[dict]]
ASGISend = Callable[[dict], Awaitable[None]]

BLOCKED_METHODS = frozenset({"DELETE", "PUT", "PATCH"})


class DemoModeMiddleware:
    """Pure ASGI middleware. Returns 403 JSON for unsafe methods.

    Blocks DELETE+PUT+PATCH (defense-in-depth — current routes have no PATCH
    but blocking it future-proofs against new mutating handlers).
    Mounted only when DEMO_MODE=true; production deploys never register this.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] in BLOCKED_METHODS:
            body = json.dumps({
                "detail": "Demo mode — destructive actions are disabled. "
                          "Self-host LightningROD to use full functionality."
            }).encode()
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
