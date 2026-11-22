"""Helpers for responses consumed by htmx."""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, Response
from markupsafe import escape
from starlette.exceptions import HTTPException

TOAST_ROOT = "#toast-root"


def error_response(message: str, status: int = 400) -> HTMLResponse:
    """A toast-bound error fragment; htmx shows it on any page."""
    body = (
        '<div class="alert alert-error text-sm" role="alert" data-auto-dismiss="6000">'
        f"<span>{escape(message)}</span></div>"
    )
    return HTMLResponse(
        body,
        status_code=status,
        headers={"HX-Retarget": TOAST_ROOT, "HX-Reswap": "beforeend"},
    )


async def _http_exception_toast(request: Request, exc: Exception) -> Response:
    """HTTPException as a toast for htmx requests, FastAPI's JSON otherwise."""
    assert isinstance(exc, HTTPException)
    if "HX-Request" in request.headers:
        return error_response(str(exc.detail), exc.status_code)
    return await http_exception_handler(request, exc)


def install_error_handler(app: FastAPI) -> None:
    """Route every HTTPException through the toast handler."""
    app.add_exception_handler(HTTPException, _http_exception_toast)
