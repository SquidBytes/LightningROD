"""Environment-backed application settings."""

from typing import Any, cast

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_port: int = 8000
    debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP_DEBUG", "LIGHTNINGROD_DEBUG"),
    )


settings = Settings.model_validate(cast(dict[str, Any], {}))
