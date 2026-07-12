"""Settings endpoint — read and update app configuration."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.services.settings_svc import get_all_settings, update_settings

router = APIRouter()


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str | None = Field(default=None, max_length=80)
    app_subtitle: str | None = Field(default=None, max_length=160)
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("app_name", "app_subtitle")
    @classmethod
    def text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("setting cannot be blank")
        return stripped

    @model_validator(mode="after")
    def payload_must_not_be_empty(self) -> "SettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one setting is required")
        return self


@router.get("")
def read_settings() -> dict[str, Any]:
    """Return current application settings."""
    return get_all_settings()


@router.put("")
def write_settings(updates: SettingsUpdate) -> dict[str, Any]:
    """Update one or more settings and return the full updated dict."""
    return update_settings(updates.model_dump(exclude_none=True))
