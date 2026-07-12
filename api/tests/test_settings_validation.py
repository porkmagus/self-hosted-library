import pytest
from pydantic import ValidationError

from api.routers.settings import SettingsUpdate


def test_settings_update_accepts_valid_values() -> None:
    update = SettingsUpdate(
        app_name="My Library",
        app_subtitle="Research at home",
        accent_color="#a1B2c3",
    )
    assert update.model_dump(exclude_none=True)["accent_color"] == "#a1B2c3"


@pytest.mark.parametrize("color", ["gold", "#fff", "#12345g", "#12345678"])
def test_settings_update_rejects_invalid_accent_color(color: str) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdate(accent_color=color)


def test_settings_update_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        SettingsUpdate.model_validate({"unknown": "value"})


def test_settings_update_rejects_empty_payload() -> None:
    with pytest.raises(ValidationError):
        SettingsUpdate()


def test_settings_update_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        SettingsUpdate(app_name="   ")
