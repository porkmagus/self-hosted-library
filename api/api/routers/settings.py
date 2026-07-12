"""Settings endpoint — read and update app configuration."""

from typing import Any

from fastapi import APIRouter

from api.services.settings_svc import get_all_settings, update_settings

router = APIRouter()


@router.get("")
def read_settings() -> dict[str, Any]:
    """Return current application settings."""
    return get_all_settings()


@router.put("")
def write_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Update one or more settings and return the full updated dict."""
    return update_settings(updates)
