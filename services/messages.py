"""Messaging helpers for Case Organizer."""

from __future__ import annotations

import sqlite3
from typing import Iterable, List

from services.db import get_app_db


def create_message(sender_id: int, recipient_id: int, subject: str, body: str) -> int:
    if sender_id == recipient_id:
        raise ValueError("Cannot send a message to yourself")
    conn = get_app_db()
    cur = conn.execute(
        """
        INSERT INTO user_messages(sender_id, recipient_id, subject, body)
        VALUES(?, ?, ?, ?)
        """,
        (sender_id, recipient_id, subject.strip(), body.strip()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_inbox(user_id: int, limit: int = 50) -> List[sqlite3.Row]:
    conn = get_app_db()
    return conn.execute(
        """
        SELECT m.*, u.email AS sender_email
        FROM user_messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.recipient_id = ?
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()


def list_sent(user_id: int, limit: int = 50) -> List[sqlite3.Row]:
    conn = get_app_db()
    return conn.execute(
        """
        SELECT m.*, u.email AS recipient_email
        FROM user_messages m
        JOIN users u ON u.id = m.recipient_id
        WHERE m.sender_id = ?
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()


def count_unread(user_id: int) -> int:
    conn = get_app_db()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM user_messages WHERE recipient_id = ? AND is_read = 0",
        (user_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def mark_message_read(message_id: int, user_id: int) -> None:
    conn = get_app_db()
    conn.execute(
        "UPDATE user_messages SET is_read = 1 WHERE id = ? AND recipient_id = ?",
        (message_id, user_id),
    )
    conn.commit()


def get_message(message_id: int, user_id: int) -> sqlite3.Row | None:
    conn = get_app_db()
    return conn.execute(
        """
        SELECT m.*, su.email AS sender_email, ru.email AS recipient_email
        FROM user_messages m
        JOIN users su ON su.id = m.sender_id
        JOIN users ru ON ru.id = m.recipient_id
        WHERE m.id = ? AND (m.recipient_id = ? OR m.sender_id = ?)
        """,
        (message_id, user_id, user_id),
    ).fetchone()


def delete_message(message_id: int, user_id: int) -> bool:
    """Remove a message when the user owns it as sender or recipient."""
    conn = get_app_db()
    cur = conn.execute(
        """
        DELETE FROM user_messages
        WHERE id = ? AND (sender_id = ? OR recipient_id = ?)
        """,
        (message_id, user_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0
