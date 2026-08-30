"""
Field-level encryption utility for sensitive data.

Wraps Fernet (AES-128-CBC + HMAC-SHA256) using the shared ENCRYPTION_KEY
from config.  Provides helpers for encrypting/decrypting individual document
fields and for stripping encrypted values from API responses.

Usage:
    from utils.encryption import encrypt_field, decrypt_field, encrypt_document_fields

    # Encrypt before writing to MongoDB
    doc["mail_password"] = encrypt_field(plaintext)

    # Decrypt after reading from MongoDB
    plaintext = decrypt_field(doc.get("mail_password"))

    # Bulk-encrypt a dict of fields
    sensitive = encrypt_document_fields(
        data,
        fields=["mail_password", "account_number", "bsb"]
    )
"""
from __future__ import annotations

import logging
from cryptography.fernet import Fernet, InvalidToken
from typing import Any, Dict, List, Optional

from config import ENCRYPTION_KEY

logger = logging.getLogger(__name__)

# ─── Initialise cipher ────────────────────────────────────────────────────────

_cipher: Optional[Fernet] = None


def _get_cipher() -> Fernet:
    """Generated function header.

    Function: _get_cipher
    Path: backend/utils/encryption.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    global _cipher
    if _cipher is None:
        key = ENCRYPTION_KEY
        if not key:
            raise RuntimeError("ENCRYPTION_KEY is not configured")
        # Fernet requires a 32-byte base64url-encoded key
        _cipher = Fernet(key.encode() if isinstance(key, str) else key)
    return _cipher


# ─── Core helpers ─────────────────────────────────────────────────────────────

# Prefix used to distinguish encrypted tokens from plaintext values
_ENC_PREFIX = "enc:"


def encrypt_field(value: Optional[str]) -> Optional[str]:
    """Encrypt *value* and return a prefixed ciphertext string.

    Returns ``None`` if *value* is ``None`` or empty.
    Already-encrypted values (prefixed with ``enc:``) are returned unchanged.
    """
    if not value:
        return value
    if isinstance(value, str) and value.startswith(_ENC_PREFIX):
        return value  # idempotent
    try:
        cipher = _get_cipher()
        token = cipher.encrypt(value.encode("utf-8"))
        return _ENC_PREFIX + token.decode("utf-8")
    except Exception as exc:  # pragma: no cover
        logger.error("encrypt_field failed: %s", exc)
        raise


def decrypt_field(value: Optional[str]) -> Optional[str]:
    """Decrypt *value* and return the plaintext string.

    Returns ``None`` if *value* is ``None`` or empty.
    Plaintext values (not prefixed) are returned unchanged so callers are safe
    even when mixing encrypted and unencrypted DB documents during migration.
    """
    if not value:
        return value
    if isinstance(value, str) and not value.startswith(_ENC_PREFIX):
        return value  # not encrypted — legacy or plaintext
    try:
        cipher = _get_cipher()
        token = value[len(_ENC_PREFIX):]
        return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("decrypt_field: invalid token — data may be corrupt or key mismatch")
        return None
    except Exception as exc:  # pragma: no cover
        logger.error("decrypt_field failed: %s", exc)
        raise


def is_encrypted(value: Optional[str]) -> bool:
    """Return True if *value* is an encrypted ciphertext produced by this module."""
    return bool(value and isinstance(value, str) and value.startswith(_ENC_PREFIX))


# ─── Bulk document helpers ────────────────────────────────────────────────────

def encrypt_document_fields(
        document: Dict[str, Any],
        fields: List[str],
) -> Dict[str, Any]:
    """Return a **copy** of *document* with each field in *fields* encrypted.

    Fields that are ``None`` or already encrypted are skipped safely.
    """
    result = dict(document)
    for field in fields:
        if field in result and result[field] is not None:
            result[field] = encrypt_field(str(result[field]))
    return result


def decrypt_document_fields(
        document: Dict[str, Any],
        fields: List[str],
) -> Dict[str, Any]:
    """Return a **copy** of *document* with each field in *fields* decrypted.

    Safe to call on documents that predate encryption (plaintext passthrough).
    """
    result = dict(document)
    for field in fields:
        if field in result and result[field] is not None:
            result[field] = decrypt_field(str(result[field]))
    return result


def redact_document_fields(
        document: Dict[str, Any],
        fields: List[str],
        redacted_value: str = "***",
) -> Dict[str, Any]:
    """Return a **copy** of *document* with sensitive fields replaced by a
    redaction placeholder (useful for API responses that should never expose
    raw passwords or account numbers).
    """
    result = dict(document)
    for field in fields:
        if field in result and result[field] is not None:
            result[field] = redacted_value
    return result


# ─── Predefined sensitive field lists ────────────────────────────────────────
# Centralise these so every router uses the same list.

SENSITIVE_USER_FIELDS: List[str] = ["mail_password"]

SENSITIVE_BANK_FIELDS: List[str] = [
    "account_number",
    "bsb_number",
    "account_name",
]

SENSITIVE_TRUST_FIELDS: List[str] = [
    "account_number",
    "bsb",
]
