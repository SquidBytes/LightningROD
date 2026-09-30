"""FordConnect settings card, Connect Vehicle flow, and the OAuth callback."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings as app_settings
from web.dependencies import get_db
from web.services.ingestion import supervisor
from web.services.secrets import mask_secret
from web.services.sources.config_store import find_row, merge_into_row, read_row
from web.services.sources.fcon import DATA_SOURCE
from web.services.sources.fcon.client import FordClient
from web.services.sources.fcon.config import FconConfig
from web.services.sources.fcon.consent import (
    SOURCE_NAME,
    finish_consent,
    start_consent,
)
from web.services.sources.fcon.oauth import CALLBACK_PATH, redirect_uri_for
from web.services.sources.fcon.retention import (
    estimate,
    get_retention_days,
    set_retention_days,
)
from web.services.sources.fcon.tokens import TOKEN_FIELDS

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

CARD = "settings/partials/fcon_card.html"
ESTIMATE = "settings/partials/fcon_retention_estimate.html"
async def get_ford_client() -> AsyncIterator[FordClient]:
    async with FordClient() as client:
        yield client


def _status(values: dict[str, Any], unreadable: bool) -> str:
    if unreadable:
        return "needs_reauth"
    if not values.get("client_id"):
        return "not_configured"
    if values.get("needs_consent"):
        return "consent_expired"
    if values.get("refresh_token"):
        return "connected"
    return "ready"


async def fcon_card_context(request: Request, db: AsyncSession, **extra: Any) -> dict:
    """Everything the FordConnect card renders; `extra` carries one-off messages."""
    row = await find_row(db, SOURCE_NAME)
    opened = read_row(row)
    values = opened.values
    derived, guessed = redirect_uri_for(
        request.url.hostname, request.url.port, app_settings.app_port
    )
    runtime = supervisor.get_runtime(SOURCE_NAME, "default")
    retention_days = await get_retention_days(db)
    return {
        "source": DATA_SOURCE,
        "status": _status(values, bool(opened.unreadable)),
        "client_id": values.get("client_id", ""),
        "masked_secret": mask_secret(values.get("client_secret", "")),
        "redirect_uri": values.get("redirect_uri") or derived,
        "redirect_guessed": guessed and not values.get("redirect_uri"),
        "refresh_expires_at": values.get("refresh_expires_at"),
        "health": runtime.health if runtime is not None else {},
        "retention_days": retention_days,
        "estimate": await estimate(db, retention_days),
        **extra,
    }


async def _card(request: Request, db: AsyncSession, **extra: Any):
    # Always 200: htmx discards 4xx bodies, which would hide the card's error message.
    return templates.TemplateResponse(
        request, CARD, {"card": await fcon_card_context(request, db, **extra)}
    )


async def _restart(db: AsyncSession) -> None:
    row = await find_row(db, SOURCE_NAME)
    if row is not None:
        await supervisor.restart_runtime(row.id)


@router.get("/settings/fcon", response_class=HTMLResponse)
async def fcon_card(request: Request, db: AsyncSession = Depends(get_db)):
    return await _card(request, db)


@router.post("/settings/fcon/credentials", response_class=HTMLResponse)
async def save_credentials(
    request: Request,
    client_id: str = Form(""),
    client_secret: str = Form(""),
    redirect_uri: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    stored = read_row(await find_row(db, SOURCE_NAME)).values
    old_secret = stored.get("client_secret", "")
    # The masked placeholder coming back unchanged means "keep the secret".
    if old_secret and client_secret == mask_secret(old_secret):
        client_secret = old_secret
    try:
        candidate = FconConfig(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
    except ValidationError:
        return await _card(
            request,
            db,
            error="Client ID, Client Secret and the redirect address are all required.",
        )
    updates: dict[str, Any] = candidate.model_dump(
        mode="json", include={"client_id", "client_secret", "redirect_uri"}
    )
    if (stored.get("client_id"), old_secret) != (candidate.client_id, candidate.client_secret):
        # Tokens belong to the Ford app that issued them.
        updates.update({k: None for k in TOKEN_FIELDS})
        updates["needs_consent"] = False
    await merge_into_row(db, SOURCE_NAME, updates)
    await _restart(db)
    return await _card(request, db, message="Saved.")


@router.post("/settings/fcon/connect", response_class=HTMLResponse)
async def connect_vehicle(request: Request, db: AsyncSession = Depends(get_db)):
    url = await start_consent(db)
    if url is None:
        return await _card(
            request, db, error="Save your Client ID and Client Secret first."
        )
    return await _card(request, db, authorize_url=url)


@router.post("/settings/fcon/finish", response_class=HTMLResponse)
async def finish_connect(
    request: Request,
    pasted: str = Form(""),
    db: AsyncSession = Depends(get_db),
    client: FordClient = Depends(get_ford_client),
):
    result = await finish_consent(db, pasted, client)
    if not result.ok:
        return await _card(request, db, paste_error=result.message, pasting=True)
    await _restart(db)
    return await _card(request, db, message=result.message)


@router.get("/settings/fcon/retention-estimate", response_class=HTMLResponse)
async def retention_estimate(
    request: Request,
    days: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    return templates.TemplateResponse(
        request, ESTIMATE, {"estimate": await estimate(db, max(days, 0))}
    )


@router.post("/settings/fcon/retention", response_class=HTMLResponse)
async def save_retention(
    request: Request,
    days: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await set_retention_days(db, days)
    return await _card(request, db, message="Saved.")


@router.post("/settings/fcon/disconnect", response_class=HTMLResponse)
async def disconnect(request: Request, db: AsyncSession = Depends(get_db)):
    row = await find_row(db, SOURCE_NAME)
    if row is not None:
        await supervisor.stop_runtime(row.id)
        updates: dict[str, Any] = {k: None for k in TOKEN_FIELDS}
        updates["needs_consent"] = False
        await merge_into_row(db, SOURCE_NAME, updates, row=row)
    return await _card(request, db, message="Disconnected. History is kept.")


@router.get(CALLBACK_PATH, response_class=HTMLResponse)
async def ford_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client: FordClient = Depends(get_ford_client),
) -> Response:
    """Ford redirected a browser on this machine here: finish without a paste."""
    result = await finish_consent(db, str(request.url), client)
    if result.ok:
        await _restart(db)
        return RedirectResponse("/settings?tab=data_sources", status_code=303)
    return templates.TemplateResponse(
        request,
        "settings/fcon_callback.html",
        {"message": result.message, "active_page": "settings"},
        status_code=400,
    )
