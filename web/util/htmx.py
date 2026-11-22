"""Helpers for responses consumed by htmx."""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
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


def _is_htmx(request: Request) -> bool:
    return "HX-Request" in request.headers


async def _http_exception_toast(request: Request, exc: Exception) -> Response:
    """HTTPException as a toast for htmx requests, FastAPI's JSON otherwise."""
    assert isinstance(exc, HTTPException)
    if not _is_htmx(request):
        return await http_exception_handler(request, exc)
    response = error_response(str(exc.detail), exc.status_code)
    response.headers.update(exc.headers or {})
    return response


async def _validation_toast(request: Request, exc: Exception) -> Response:
    """Rejected request fields as a toast for htmx requests, JSON otherwise."""
    assert isinstance(exc, RequestValidationError)
    if not _is_htmx(request):
        return await request_validation_exception_handler(request, exc)
    message = "; ".join(f"{error['loc'][-1]}: {error['msg']}" for error in exc.errors())
    return error_response(message, 422)


def install_error_handler(app: FastAPI) -> None:
    """Route HTTPException and request validation errors through the toast handlers."""
    app.add_exception_handler(HTTPException, _http_exception_toast)
    app.add_exception_handler(RequestValidationError, _validation_toast)
