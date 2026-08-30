"""
Cryptographic utilities for sensitive field encryption.

Thin compatibility wrapper around utils/encryption.py.
Uses the existing ENCRYPTION_KEY from config.py (already used by Asset Register
and the full-featured encryption module).

Callers that were written against the gaps_and_crypt_issues.md specification can
import from here; internally everything delegates to utils.encryption so there is
a single source of truth for key management and token format.
"""
from __future__ import annotations

from utils.encryption import (
    encrypt_field as _encrypt_field,
    decrypt_field as _decrypt_field,
    is_encrypted as _is_encrypted,
)


def encrypt_sensitive(plain_text: str) -> str:
    """Encrypt a sensitive string field for database storage.

    Returns the value unchanged if it is None/empty.
    Already-encrypted values (prefixed with 'enc:') are returned unchanged
    (idempotent — safe to call multiple times).
    """
    return _encrypt_field(plain_text)  # type: ignore[return-value]


def decrypt_sensitive(cipher_text: str) -> str:
    """Decrypt a sensitive string field retrieved from database.

    Returns the value unchanged if it is not encrypted (backward-compatible
    during the migration window when some values may still be plain text).
    Returns None if the token is corrupt / key mismatch.
    """
    return _decrypt_field(cipher_text)  # type: ignore[return-value]


def is_encrypted(value: str) -> bool:
    """Return True if *value* is a ciphertext produced by this module."""
    return _is_encrypted(value)


__all__ = ["encrypt_sensitive", "decrypt_sensitive", "is_encrypted"]
