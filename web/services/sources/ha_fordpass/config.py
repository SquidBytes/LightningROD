"""Per-source config schema for ha_fordpass.

Mirrors the existing HA-related settings keys (ha_url, ha_token, ha_vin_override,
ha_unit_system, ha_auto_connect). The home_* keys stay in app_settings as
global settings and do not belong here.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class HAFordpassConfig(BaseModel):
    ha_url: str = Field(min_length=1)
    ha_token: str = Field(min_length=1)
    ha_vin_override: str | None = None
    ha_unit_system: Literal["auto", "metric", "imperial"] = "auto"
    ha_auto_connect: bool = True

    @field_validator("ha_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")
