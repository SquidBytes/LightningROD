"""Start and finish Connect Vehicle against the stored fcon config row."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from web.services.sources.config_store import find_row, merge_into_row, read_row
from web.services.sources.fcon.client import FordClient, FordError, authorize_url
from web.services.sources.fcon.config import FconConfig
from web.services.sources.fcon.oauth import (
    PasteRejected,
    explain_token_error,
    new_state,
    parse_returned,
)
from web.services.sources.fcon.tokens import exchange_code, token_fields

SOURCE_NAME = "fcon"
_NO_CREDENTIALS = "Save your Client ID and Client Secret first."


@dataclass(frozen=True)
class ConsentResult:
    ok: bool
    message: str
    config_id: int | None = None


async def load_config(db: AsyncSession) -> FconConfig | None:
    try:
        return FconConfig.model_validate(read_row(await find_row(db, SOURCE_NAME)).values)
    except ValidationError:
        return None


async def start_consent(db: AsyncSession) -> str | None:
    """Record a fresh state and return the Ford sign-in URL; None without credentials."""
    config = await load_config(db)
    if config is None:
        return None
    state = new_state()
    await merge_into_row(
        db,
        SOURCE_NAME,
        {"oauth_state": state, "oauth_state_at": datetime.now(UTC).isoformat()},
    )
    return authorize_url(config.client_id, config.redirect_uri, state)


async def finish_consent(db: AsyncSession, returned: str, client: FordClient) -> ConsentResult:
    """Validate what came back from Ford, exchange the code, store the tokens."""
    row = await find_row(db, SOURCE_NAME)
    try:
        config = FconConfig.model_validate(read_row(row).values)
    except ValidationError:
        return ConsentResult(False, _NO_CREDENTIALS)
    try:
        code = parse_returned(
            returned,
            expected_state=config.oauth_state,
            state_created_at=config.oauth_state_at,
        )
        connected = await exchange_code(client, config, code.code)
    except PasteRejected as exc:
        return ConsentResult(False, str(exc))
    except FordError as exc:
        return ConsentResult(False, explain_token_error(exc))
    row = await merge_into_row(db, SOURCE_NAME, token_fields(connected), row=row)
    return ConsentResult(True, "Vehicle connected.", config_id=row.id)
