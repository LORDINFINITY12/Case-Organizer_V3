"""User management helpers for Case Organizer."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from services.db import get_app_db
from services.security import hash_password, verify_password


class UserExistsError(ValueError):
    """Raised when attempting to create a user with an email that already exists."""


class EmailInUseError(UserExistsError):
    """Raised when updating a user to an email that already exists."""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def create_user(email: str, password: str, role: str = "user", is_active: bool = True) -> int:
    email_norm = normalize_email(email)
    if not email_norm:
        raise ValueError("Email is required")
    if role not in {"admin", "user"}:
        raise ValueError("Invalid role")
    password_hash = hash_password(password)

    conn = get_app_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO users(email, password_hash, role, is_active)
            VALUES(?, ?, ?, ?)
            """,
            (email_norm, password_hash, role, 1 if is_active else 0),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise UserExistsError(f"User with email {email_norm!r} already exists") from exc

    return int(cur.lastrowid)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    email_norm = normalize_email(email)
    if not email_norm:
        return None
    conn = get_app_db()
    return conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email_norm,),
    ).fetchone()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    conn = get_app_db()
    return conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def authenticate_user(email: str, password: str) -> Optional[sqlite3.Row]:
    user = get_user_by_email(email)
    if not user or not user["is_active"]:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def count_users() -> int:
    conn = get_app_db()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    return int(row["c"] if row else 0)


def set_user_password(user_id: int, password: str) -> None:
    conn = get_app_db()
    conn.execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (hash_password(password), user_id),
    )
    conn.commit()


def mark_user_login(user_id: int) -> None:
    conn = get_app_db()
    conn.execute(
        "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id,),
    )
    conn.commit()


def create_password_reset_token(user_id: int, expires_minutes: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=expires_minutes)).isoformat()

    conn = get_app_db()
    conn.execute("DELETE FROM password_resets WHERE user_id = ? AND consumed_at IS NULL", (user_id,))
    conn.execute(
        """
        INSERT INTO password_resets(user_id, token, expires_at)
        VALUES(?, ?, ?)
        """,
        (user_id, token, expires_at),
    )
    conn.commit()
    return token


def get_password_reset(token: str) -> Optional[sqlite3.Row]:
    if not token:
        return None
    conn = get_app_db()
    row = conn.execute(
        "SELECT * FROM password_resets WHERE token = ?",
        (token,),
    ).fetchone()
    if not row or row["consumed_at"] is not None:
        return None
    expires_at_raw = row["expires_at"]
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except Exception:
        return None
    if expires_at < datetime.utcnow():
        return None
    return row


def consume_password_reset(reset_id: int) -> None:
    conn = get_app_db()
    conn.execute(
        "UPDATE password_resets SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (reset_id,),
    )
    conn.commit()


def list_users() -> list[sqlite3.Row]:
    conn = get_app_db()
    return conn.execute(
        "SELECT id, email, role, is_active, created_at, updated_at, last_login_at FROM users ORDER BY email COLLATE NOCASE"
    ).fetchall()


def set_user_active(user_id: int, active: bool) -> None:
    conn = get_app_db()
    conn.execute(
        "UPDATE users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (1 if active else 0, user_id),
    )
    conn.commit()


def count_admins(active_only: bool = True) -> int:
    conn = get_app_db()
    if active_only:
        row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = 1").fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'").fetchone()
    return int(row["c"] if row else 0)


def update_user_email(user_id: int, new_email: str) -> None:
    email_norm = normalize_email(new_email)
    if not email_norm:
        raise ValueError("Email must not be empty")
    conn = get_app_db()
    try:
        conn.execute(
            "UPDATE users SET email = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (email_norm, user_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise EmailInUseError(f"Email {email_norm!r} is already registered") from exc


def update_user_role(user_id: int, new_role: str) -> None:
    if new_role not in {"admin", "user"}:
        raise ValueError("Invalid role")
    conn = get_app_db()
    conn.execute(
        "UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_role, user_id),
    )
    conn.commit()
