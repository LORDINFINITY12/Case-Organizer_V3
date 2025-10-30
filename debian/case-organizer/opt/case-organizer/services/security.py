"""Security helpers for password hashing and verification."""

from __future__ import annotations

from argon2 import PasswordHasher, exceptions as argon_exc

ph = PasswordHasher()


def hash_password(plain_text: str) -> str:
    """Create an Argon2 hash for the provided password."""
    if not plain_text:
        raise ValueError("Password must not be empty")
    return ph.hash(plain_text)


def verify_password(plain_text: str, hashed: str) -> bool:
    """Verify a candidate password against a stored Argon2 hash."""
    if not plain_text or not hashed:
        return False
    try:
        return ph.verify(hashed, plain_text)
    except (argon_exc.VerificationError, argon_exc.InvalidHash):
        return False
