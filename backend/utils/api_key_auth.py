"""
API Key Authentication Utilities

Provides API key generation, validation, and scope-based access control
for external integrations with the multi-tenant strata management platform.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from dateutil.parser import parse as _parse_dt
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from database import db
from request_context import set_ctx_building_id

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEY_PREFIX = "egst_"  # historical prefix — do not change; existing keys start with this value
VALID_SCOPES = {
    "read:building",  # Read building and unit data
    "read:finance",  # Read financial/accounting data
    "read:maintenance",  # Read maintenance requests and defects
    "write:maintenance",  # Create/update maintenance requests
    "read:work-orders",  # Read work orders
    "write:work-orders",  # Update work orders (service providers)
    "write:service",  # Service provider operations (quotes, invoices)
    "manage:webhooks",  # Register and manage webhooks
    "admin",  # Full access (includes all scopes above)
}


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key and its SHA-256 hashed form.

    The raw key is shown once on creation; only the hash is stored.
    Returns (raw_key, hashed_key).
    """
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def _has_scope(key_doc: dict, required_scope: str) -> bool:
    """Return True if the API key has the required scope or admin scope."""
    scopes: list = key_doc.get("scopes", [])
    return "admin" in scopes or required_scope in scopes


async def get_api_key_auth(api_key: str = Security(API_KEY_HEADER)) -> dict:
    """
    FastAPI dependency: authenticate an external request via API key.

    Looks up the hashed key in the ``api_keys`` collection, checks
    expiry, updates ``last_used_at``, and returns the key document.
    Raises HTTP 401 when the key is missing, invalid, or expired.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Include X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    hashed = hashlib.sha256(api_key.encode()).hexdigest()
    key_doc = await db.api_keys.find_one(
        {"key_hash": hashed, "is_active": True}, {"_id": 0}
    )

    if not key_doc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Check expiration if set
    if key_doc.get("expires_at"):
        try:
            exp = _parse_dt(key_doc["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired.",
                    headers={"WWW-Authenticate": "ApiKey"},
                )
        except (ValueError, AttributeError):
            pass

    # Resolve building context for tenant-scoped collections
    building_id = await resolve_building_id_for_key(key_doc)
    if not building_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No active building configured for API key access",
        )

    set_ctx_building_id(building_id)
    key_doc["building_id"] = building_id

    # Record last use (fire-and-forget style via direct update)
    await db.api_keys.update_one(
        {"key_hash": hashed},
        {"$set": {"last_used_at": datetime.now(timezone.utc).isoformat()}},
    )

    return key_doc


def require_scope(scope: str):
    """
    Returns a FastAPI dependency that verifies the API key has ``scope``.
    Usage::

        @router.get("/something")
        async def endpoint(key: dict = Depends(require_scope("read:building"))):
            ...
    """

    async def _check(key_doc: dict = Security(get_api_key_auth)):
        """Generated function header.

        Function: _check
        Path: backend/utils/api_key_auth.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not _has_scope(key_doc, scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key does not have required scope: {scope}",
            )
        return key_doc

    return _check


async def resolve_building_id_for_key(key_doc: dict | None = None) -> str | None:
    """Resolve the building_id for an API key, falling back to the first active building."""
    if key_doc and key_doc.get("building_id"):
        return key_doc["building_id"]

    building = await db.buildings.find_one(
        {"is_active": True},
        {"_id": 0, "id": 1},
        sort=[("created_at", 1)],
    )
    if building and building.get("id"):
        return building["id"]
    return None


__all__ = [
    "API_KEY_HEADER",
    "API_KEY_PREFIX",
    "VALID_SCOPES",
    "generate_api_key",
    "get_api_key_auth",
    "resolve_building_id_for_key",
    "require_scope",
]
