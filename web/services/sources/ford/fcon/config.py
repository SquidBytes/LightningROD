"""Per-source config schema for fcon — one row per connected vehicle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FconConfig(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    # Replayed byte-identically in the authorize call and the token exchange.
    redirect_uri: str = Field(min_length=1)

    # Pending consent: Ford truncates `state` to 16 chars, so it is an opaque
    # key checked against this row rather than a signed token.
    oauth_state: str | None = None
    oauth_state_at: datetime | None = None

    access_token: str | None = None
    refresh_token: str | None = None
    id_token: str | None = None
    token_type: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    obtained_at: datetime | None = None
    # Set when Ford rejects the refresh token: only a new consent fixes it.
    needs_consent: bool = False

    @field_validator("client_id", "client_secret", "redirect_uri")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token) and not self.needs_consent

    def with_token(self, token: dict[str, Any], now: datetime | None = None) -> FconConfig:
        """Copy carrying a fresh token response; clears any pending consent."""
        now = now or datetime.now(UTC)
        refresh_in = token.get("refresh_token_expires_in")
        return self.model_copy(
            update={
                "access_token": token["access_token"],
                # Ford may or may not rotate the refresh token.
                "refresh_token": token.get("refresh_token") or self.refresh_token,
                "id_token": token.get("id_token") or self.id_token,
                "token_type": token.get("token_type"),
                "scope": token.get("scope"),
                "expires_at": now + timedelta(seconds=int(token.get("expires_in", 1200))),
                "refresh_expires_at": (
                    now + timedelta(seconds=int(refresh_in))
                    if refresh_in
                    else self.refresh_expires_at
                ),
                "obtained_at": now,
                "oauth_state": None,
                "oauth_state_at": None,
                "needs_consent": False,
            }
        )
