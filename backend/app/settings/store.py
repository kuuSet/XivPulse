"""Persistence helpers for settings stored in SQLite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def load_setting_values(db: Session) -> dict[str, Any]:
    from ..models import AppSetting

    settings = db.scalars(select(AppSetting)).all()
    return {item.key: item.value_json for item in settings}


def setting_value(db: Session, key: str, default: Any = None) -> Any:
    from ..models import AppSetting

    setting = db.get(AppSetting, key)
    return setting.value_json if setting is not None else default


def set_setting(db: Session, key: str, value: Any) -> None:
    from ..models import AppSetting

    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value_json=value))
    else:
        setting.value_json = value


def delete_setting(db: Session, key: str) -> None:
    from ..models import AppSetting

    setting = db.get(AppSetting, key)
    if setting is not None:
        db.delete(setting)


__all__ = [
    "delete_setting",
    "load_setting_values",
    "set_setting",
    "setting_value",
]
