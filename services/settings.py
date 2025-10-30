"""Centralised configuration and secret management for Case Organizer."""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def _default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "case-organizer"
    return Path.home() / ".config" / "case-organizer"


@dataclass
class SettingsPaths:
    config_dir: Path
    settings_file: Path
    secrets_file: Path


class SettingsManager:
    """Handles persistence of application settings and encrypted secrets."""

    SETTINGS_SCHEMA_VERSION = 1

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.paths = SettingsPaths(
            config_dir=config_dir or _default_config_dir(),
            settings_file=(config_dir or _default_config_dir()) / "settings.json",
            secrets_file=(config_dir or _default_config_dir()) / "secrets.enc",
        )
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)

        self._settings: Dict[str, Any] = {}
        self._load_settings()

        env_passphrase = os.environ.get("CASEORG_SECRET_KEY")
        self.default_passphrase: Optional[str] = env_passphrase
        if not self.default_passphrase:
            self.default_passphrase = self._load_or_create_master_key()

        self._ensure_schema()

    # ------------------------------------------------------------------
    # Public API for plain settings
    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._save_settings()

    def delete(self, key: str) -> None:
        if key in self._settings:
            del self._settings[key]
            self._save_settings()

    # ------------------------------------------------------------------
    # Secret handling
    # ------------------------------------------------------------------
    def get_secret(self, key: str, default: Any = None, passphrase: Optional[str] = None) -> Any:
        payload = self._load_secrets(passphrase)
        if payload is None:
            return default
        return payload.get(key, default)

    def set_secret(self, key: str, value: Any, passphrase: Optional[str] = None) -> None:
        payload = self._load_secrets(passphrase) or {}
        payload[key] = value
        self._store_secrets(payload, passphrase)

    def delete_secret(self, key: str, passphrase: Optional[str] = None) -> None:
        payload = self._load_secrets(passphrase)
        if not payload or key not in payload:
            return
        payload.pop(key)
        self._store_secrets(payload, passphrase)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        try:
            with self.paths.settings_file.open("r", encoding="utf-8") as fh:
                self._settings = json.load(fh)
        except FileNotFoundError:
            self._settings = {}
        except json.JSONDecodeError:
            raise RuntimeError("settings.json is corrupted; please repair or delete it.")

    def _save_settings(self) -> None:
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        with self.paths.settings_file.open("w", encoding="utf-8") as fh:
            json.dump(self._settings, fh, indent=2, sort_keys=True)

    def _ensure_schema(self) -> None:
        version = int(self._settings.get("schema_version", 0))
        if version < 1:
            self._settings.setdefault("schema_version", self.SETTINGS_SCHEMA_VERSION)
            self._settings.setdefault("secret_iterations", 390_000)
            if "secret_salt" not in self._settings:
                salt = os.urandom(16)
                self._settings["secret_salt"] = base64.urlsafe_b64encode(salt).decode("utf-8")
            self._save_settings()

    def _load_or_create_master_key(self) -> str:
        key_path = self.paths.config_dir / "master.key"
        try:
            key = key_path.read_text(encoding="utf-8").strip()
            if key:
                return key
        except FileNotFoundError:
            pass

        key = secrets.token_urlsafe(32)
        key_path.write_text(key, encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except Exception:
            pass
        return key

    def _load_secrets(self, passphrase: Optional[str]) -> Optional[Dict[str, Any]]:
        if not self.paths.secrets_file.exists():
            return {}

        key = self._derive_key(passphrase)
        if key is None:
            raise RuntimeError(
                "Secret passphrase required. Set CASEORG_SECRET_KEY or provide passphrase explicitly."
            )

        fernet = Fernet(key)
        try:
            token = self.paths.secrets_file.read_bytes()
            decrypted = fernet.decrypt(token)
        except InvalidToken as exc:
            raise RuntimeError("Unable to decrypt secrets store. Invalid passphrase?") from exc

        try:
            return json.loads(decrypted.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Secrets store is corrupted.") from exc

    def _store_secrets(self, payload: Dict[str, Any], passphrase: Optional[str]) -> None:
        key = self._derive_key(passphrase)
        if key is None:
            raise RuntimeError(
                "Secret passphrase required. Set CASEORG_SECRET_KEY or provide passphrase explicitly."
            )
        fernet = Fernet(key)
        data = json.dumps(payload).encode("utf-8")
        token = fernet.encrypt(data)
        self.paths.secrets_file.write_bytes(token)

    def _derive_key(self, passphrase: Optional[str]) -> Optional[bytes]:
        actual = passphrase or self.default_passphrase
        if not actual:
            return None
        salt_b64 = self._settings.get("secret_salt")
        if not salt_b64:
            raise RuntimeError("Settings missing secret salt; try reinitialising configuration.")
        salt = base64.urlsafe_b64decode(salt_b64)
        iterations = int(self._settings.get("secret_iterations", 390_000))
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        key = kdf.derive(actual.encode("utf-8"))
        return base64.urlsafe_b64encode(key)


settings_manager = SettingsManager()
