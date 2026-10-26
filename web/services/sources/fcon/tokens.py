"""Token exchange and the refresh loop around Ford's 20-minute access tokens."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from web.services.sources.fcon.client import FordClient, FordError, RateLimited
from web.services.sources.fcon.config import FconConfig

REFRESH_LEEWAY = timedelta(seconds=90)
TOKEN_FIELDS = (
    "access_token",
    "refresh_token",
    "id_token",
    "token_type",
    "scope",
    "expires_at",
    "refresh_expires_at",
    "obtained_at",
    "oauth_state",
    "oauth_state_at",
    "needs_consent",
)

Persist = Callable[[dict[str, Any]], Awaitable[None]]


class ConsentRequired(FordError):
    """Ford will not refresh; the user has to run Connect Vehicle again."""


def token_fields(config: FconConfig) -> dict[str, Any]:
    """The token half of a config, JSON-ready, for merging into its row."""
    dumped = config.model_dump(mode="json")
    return {k: dumped[k] for k in TOKEN_FIELDS}


def _credentials(config: FconConfig) -> dict[str, str]:
    return {"client_id": config.client_id, "client_secret": config.client_secret}


async def exchange_code(client: FordClient, config: FconConfig, code: str) -> FconConfig:
    token = await client.token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            **_credentials(config),
        }
    )
    return config.with_token(token)


class TokenManager:
    """Hands out a live access token, refreshing and persisting as needed."""

    def __init__(
        self,
        config: FconConfig,
        client: FordClient,
        persist: Persist,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._client = client
        self._persist = persist
        self._now = now

    @property
    def config(self) -> FconConfig:
        return self._config

    def invalidate(self) -> None:
        """Forget the access token so the next call refreshes — Ford said it is dead."""
        self._config = self._config.model_copy(update={"access_token": None})

    async def access_token(self) -> str:
        cfg = self._config
        if cfg.needs_consent or not cfg.refresh_token:
            raise ConsentRequired("no usable refresh token")
        if (
            cfg.access_token
            and cfg.expires_at
            and cfg.expires_at - REFRESH_LEEWAY > self._now()
        ):
            return cfg.access_token
        await self.refresh()
        assert self._config.access_token is not None
        return self._config.access_token

    async def refresh(self) -> None:
        cfg = self._config
        try:
            token = await self._client.token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": cfg.refresh_token or "",
                    **_credentials(cfg),
                }
            )
        except RateLimited:
            raise
        except FordError as exc:
            # 4xx from the token endpoint means the grant itself is dead
            # (expired after 90 days, revoked, or the secret changed).
            if exc.status is not None and 400 <= exc.status < 500:
                self._config = cfg.model_copy(update={"needs_consent": True})
                await self._persist(token_fields(self._config))
                raise ConsentRequired("refresh token rejected", status=exc.status) from exc
            raise
        self._config = cfg.with_token(token, self._now())
        await self._persist(token_fields(self._config))
