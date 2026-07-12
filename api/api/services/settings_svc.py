"""Application settings — persisted in PostgreSQL."""

from typing import Any

from sqlalchemy import text

from api.models import get_db_session

_app_settings: dict[str, str] | None = None

ALLOWED_KEYS = {"app_name", "app_subtitle", "accent_color"}


def init_settings_table() -> None:
    """Create settings table and seed defaults if empty."""
    with get_db_session() as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key   VARCHAR(64) PRIMARY KEY,
                value TEXT NOT NULL
            )
        """))
        defaults = [
            ("app_name", "Self-Hosted Library"),
            ("app_subtitle", "Private document search and research"),
            ("accent_color", "#D4AF57"),
        ]
        existing = session.execute(
            text("SELECT COUNT(*) FROM app_settings")
        ).scalar()
        if (existing or 0) == 0:
            for k, v in defaults:
                session.execute(
                    text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"),
                    {"k": k, "v": v},
                )
        session.commit()


def get_all_settings() -> dict[str, str]:
    global _app_settings
    with get_db_session() as session:
        rows = session.execute(
            text("SELECT key, value FROM app_settings")
        ).fetchall()
        _app_settings = {row[0]: row[1] for row in rows}
    return _app_settings or {}


def update_settings(updates: dict[str, Any]) -> dict[str, str]:
    global _app_settings
    for k in updates:
        if k not in ALLOWED_KEYS:
            raise ValueError(f"Unknown setting: {k}")

    with get_db_session() as session:
        for k, v in updates.items():
            session.execute(
                text("""
                    INSERT INTO app_settings (key, value) VALUES (:k, :v)
                    ON CONFLICT (key) DO UPDATE SET value = :v
                """),
                {"k": k, "v": str(v)},
            )
        session.commit()

    _app_settings = None
    return get_all_settings()
