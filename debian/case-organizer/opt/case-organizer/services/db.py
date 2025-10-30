"""Database utilities for Case Organizer 2.0."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from flask import g

import caseorg_config
from services.settings import settings_manager


# Global schema version for the application database.
_SCHEMA_VERSION = 2


def _app_db_path() -> Path:
    """Return the path for the primary application database."""
    legacy_cfg = getattr(caseorg_config, 'CASEORG_CONFIG', None)
    if legacy_cfg:
        return Path(legacy_cfg).with_name('organizer.db')
    return settings_manager.paths.config_dir / 'organizer.db'


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    row = conn.execute(
        "SELECT value FROM app_meta WHERE key = 'schema_version'"
    ).fetchone()
    current_version = int(row["value"]) if row else 0

    if current_version < 1:
        _migrate_to_v1(conn)
        current_version = 1

    if current_version < 2:
        _migrate_to_v2(conn)
        current_version = 2

    if current_version != _SCHEMA_VERSION:
        # Placeholder for future migrations.
        conn.execute(
            "INSERT INTO app_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user')),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_users_updated_at
        AFTER UPDATE ON users
        BEGIN
            UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END;
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_resets_token ON password_resets(token)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            subject TEXT,
            body TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(recipient_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_messages_recipient ON user_messages(recipient_id, is_read)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            protected INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_settings_updated_at
        AFTER UPDATE ON app_settings
        BEGIN
            UPDATE app_settings SET updated_at = CURRENT_TIMESTAMP WHERE key = NEW.key;
        END;
        """
    )

    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES('schema_version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL UNIQUE,
            case_year TEXT,
            case_month TEXT,
            case_name TEXT,
            file_path TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            generated_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(generated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_invoices_updated_at
        AFTER UPDATE ON invoices
        BEGIN
            UPDATE invoices SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END;
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoices_case ON invoices(case_year, case_month, case_name)"
    )

    conn.execute(
        """
        INSERT INTO app_settings(key, value, protected)
        VALUES('invoice_next_number', '1', 0)
        ON CONFLICT(key) DO NOTHING
        """
    )

def get_app_db() -> sqlite3.Connection:
    """Return a connection to the application database bound to Flask's context."""
    if 'app_db' not in g:
        db_path = _app_db_path()
        _ensure_parent_dir(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        _ensure_schema(conn)
        g.app_db = conn
    return g.app_db


def close_app_db(_: Optional[BaseException]) -> None:
    conn = g.pop('app_db', None)
    if conn is not None:
        conn.close()
