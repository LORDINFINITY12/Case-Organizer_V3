"""Compatibility layer for legacy imports during the 2.0 transition."""

from __future__ import annotations

import os
from typing import Any, List

from services.settings import settings_manager

_manager = settings_manager


def _get_secret_legacy(key: str) -> Any:
    try:
        return _manager.get_secret(key)
    except RuntimeError:
        # Fall back to plain value if secrets are not yet initialised.
        return _manager.get(key)


FS_ROOT = _manager.get("fs_root")
ALLOWED_USERS: List[str] = _manager.get("legacy_allowed_users", [])
PASSWORD = _get_secret_legacy("legacy_shared_password")

SECRET_KEY = (
    os.environ.get("CASEORG_SECRET_KEY")
    or _manager.get("flask_secret_key")
    or "dev-local-secret-key"
)

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "png", "jpg", "jpeg", "json"}


def save_fs_root(path_str: str) -> None:
    _manager.set("fs_root", path_str)
    global FS_ROOT
    FS_ROOT = path_str


def save_users(users_list: List[str]) -> None:
    _manager.set("legacy_allowed_users", users_list)
    global ALLOWED_USERS
    ALLOWED_USERS = users_list


def save_password(pw: str) -> None:
    try:
        _manager.set_secret("legacy_shared_password", pw)
    except RuntimeError:
        _manager.set("legacy_shared_password", pw)
    global PASSWORD
    PASSWORD = pw


def is_storage_configured() -> bool:
    return bool(_manager.get("fs_root"))


def is_users_configured() -> bool:
    return bool(_manager.get("legacy_allowed_users"))


def is_password_configured() -> bool:
    return bool(_get_secret_legacy("legacy_shared_password"))
