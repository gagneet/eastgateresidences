"""
Shared rate limiter instance for slowapi.

Import `limiter` from this module in any router or server file that
needs rate limiting.  server.py is responsible for registering the
limiter on `app.state` and adding the RateLimitExceeded exception
handler — this module only creates the shared instance.
"""

from __future__ import annotations

import inspect
import ipaddress
import os
import time
from datetime import datetime, timezone
from functools import wraps

import logging
from typing import Any, Dict, List, Optional


class _DummyLimiter:
    """No-op limiter used when slowapi is not installed."""

    def limit(self, *args, **kwargs):
        """Generated function header.

        Function: _DummyLimiter.limit
        Path: backend/utils/rate_limit.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        def decorator(func):
            """Generated function header.

            Function: _DummyLimiter.decorator
            Path: backend/utils/rate_limit.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            return func

        return decorator


# ---------------------------------------------------------------------------
# Trusted-proxy IP resolution (prevents X-Forwarded-For spoofing)
# ---------------------------------------------------------------------------

# Networks whose connecting peers are allowed to set X-Forwarded-For / X-Real-IP.
# Default: loopback only (nginx on the same host sends requests from 127.x).
# Extend at deploy time via TRUSTED_PROXY_CIDRS env var (comma-separated CIDRs).
_DEFAULT_TRUSTED_CIDRS = [
    "127.0.0.0/8",  # IPv4 loopback
    "::1/128",  # IPv6 loopback
]


def _build_trusted_networks() -> List[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Generated function header.

    Function: _build_trusted_networks
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    cidrs = list(_DEFAULT_TRUSTED_CIDRS)
    extra = os.getenv("TRUSTED_PROXY_CIDRS", "")
    for cidr in filter(None, (c.strip() for c in extra.split(","))):
        cidrs.append(cidr)
    networks: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logging.getLogger(__name__).warning(
                "TRUSTED_PROXY_CIDRS: ignoring invalid CIDR %r", cidr
            )
    return networks


_TRUSTED_PROXY_NETWORKS = _build_trusted_networks()


def is_trusted_proxy(host: str) -> bool:
    """Return True if *host* is a trusted reverse-proxy peer.

    Handles IPv6-mapped IPv4 addresses (e.g. ``::ffff:127.0.0.1``) that
    uvicorn may report on dual-stack systems, unwrapping them to their
    IPv4 form before comparing against the trusted network list.

    This is a shared utility — import it from geo.py or any other module
    that needs to validate whether a connecting peer can be trusted to set
    proxy headers like X-Real-IP or X-Forwarded-For.
    """
    try:
        addr = ipaddress.ip_address(host)
        # Unwrap IPv6-mapped IPv4 so it matches IPv4 trusted networks.
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        return any(addr in net for net in _TRUSTED_PROXY_NETWORKS)
    except ValueError:
        return False


def _get_real_ip(request: Any) -> str:
    """SlowAPI compatibility wrapper around the canonical IP resolver."""
    # Lazy import preserves the existing cycle: client_ip consults
    # is_trusted_proxy(), while this module supplies SlowAPI's key function.
    from utils.client_ip import resolve_client_ips

    return resolve_client_ips(request).best


try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address  # noqa: F401 — kept for import compat

    limiter = Limiter(key_func=_get_real_ip)
    SLOWAPI_AVAILABLE = True
except ImportError:
    limiter = _DummyLimiter()
    SLOWAPI_AVAILABLE = False

RATE_LIMIT_SETTING_ID = "rate_limits"
RATE_LIMIT_DEFAULTS: Dict[str, int] = {
    "rate_limit_register": 5,
    "rate_limit_login": 10,
    "rate_limit_forgot_password": 5,
    "rate_limit_reset_password": 5,
    "rate_limit_change_password": 20,
    "rate_limit_registration_decision": 10,
    # Unauthenticated resident sign-up invite lookup — the opaque token is the
    # only credential, and the response discloses the invitee's prefill details.
    "rate_limit_registration_invite_lookup": 20,
    # High-privilege auth actions
    "rate_limit_impersonate": 10,
    "rate_limit_totp_challenge": 10,  # unauthenticated — tightest limit
    "rate_limit_totp_verify": 20,
    "rate_limit_totp_disable": 10,
    "rate_limit_totp_setup": 10,
}

_RATE_LIMIT_STATE: Dict[str, Any] = {
    "enabled": True,
    "multiplier": 1.0,
    "limits": dict(RATE_LIMIT_DEFAULTS),
    "updated_at": None,
    "last_refreshed_epoch": 0.0,
}

_REFRESH_INTERVAL_SECONDS = 60
_logger = logging.getLogger(__name__)

# Cached at module load so the env-var check has zero overhead per request.
# Set DISABLE_RATE_LIMIT=1 in the process environment before starting the server.
_DISABLE_RATE_LIMIT: bool = os.getenv("DISABLE_RATE_LIMIT") == "1"


def get_rate_limit_state() -> Dict[str, Any]:
    """Generated function header.

    Function: get_rate_limit_state
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return dict(_RATE_LIMIT_STATE)


def update_rate_limit_state(
        *,
        enabled: Optional[bool] = None,
        multiplier: Optional[float] = None,
        limits: Optional[Dict[str, int]] = None,
) -> None:
    """Generated function header.

    Function: update_rate_limit_state
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if enabled is not None:
        _RATE_LIMIT_STATE["enabled"] = bool(enabled)
    if multiplier is not None:
        _RATE_LIMIT_STATE["multiplier"] = float(multiplier)
    if limits:
        _RATE_LIMIT_STATE["limits"].update(limits)
    _RATE_LIMIT_STATE["updated_at"] = datetime.now(timezone.utc).isoformat()
    _RATE_LIMIT_STATE["last_refreshed_epoch"] = time.time()


def _normalise_multiplier(value: Any) -> float:
    """Generated function header.

    Function: _normalise_multiplier
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 1.0
    if parsed <= 0:
        return 1.0
    return parsed


def _apply_multiplier(base: int, multiplier: float) -> int:
    """Generated function header.

    Function: _apply_multiplier
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        effective = int(round(base * multiplier))
    except (TypeError, ValueError):
        effective = base
    return max(1, effective)


def get_effective_rate_limit(limit_key: str, default: int) -> str:
    """Generated function header.

    Function: get_effective_rate_limit
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    limits = _RATE_LIMIT_STATE.get("limits", {})
    base = limits.get(limit_key, default)
    multiplier = _normalise_multiplier(_RATE_LIMIT_STATE.get("multiplier", 1.0))
    effective = _apply_multiplier(int(base), multiplier)
    return f"{effective}/minute"


def rate_limit(limit_key: str, default: int):
    """
    Dynamic rate limiter decorator that respects runtime config.

    Uses slowapi when available, falls back to a no-op when disabled.
    """

    def decorator(func):
        """Generated function header.

        Function: decorator
        Path: backend/utils/rate_limit.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not SLOWAPI_AVAILABLE:
            return func

        @wraps(func)
        async def wrapper(*args, **kwargs):
            """Generated function header.

            Function: wrapper
            Path: backend/utils/rate_limit.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            if _DISABLE_RATE_LIMIT:
                result = func(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result

            now_epoch = time.time()
            last_refresh = _RATE_LIMIT_STATE.get("last_refreshed_epoch", 0.0) or 0.0
            if now_epoch - last_refresh > _REFRESH_INTERVAL_SECONDS:
                try:
                    await refresh_rate_limit_config()
                except Exception as exc:
                    _logger.warning("Rate limit refresh failed: %s", exc)

            if not _RATE_LIMIT_STATE.get("enabled", True):
                result = func(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result

            limit_value = get_effective_rate_limit(limit_key, default)
            limited_func = limiter.limit(limit_value)(func)
            result = limited_func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        return wrapper

    return decorator


async def refresh_rate_limit_config() -> Dict[str, Any]:
    """Generated function header.

    Function: refresh_rate_limit_config
    Path: backend/utils/rate_limit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from db_postgres.repos import config_repo
    from database import db

    enabled = await config_repo.get_global_feature_toggle_state("rate_limiting", default=True)

    settings = await db.site_settings.find_one({"id": RATE_LIMIT_SETTING_ID}, {"_id": 0}) or {}
    limits: Dict[str, int] = {}
    for key, default in RATE_LIMIT_DEFAULTS.items():
        value = settings.get(key)
        if isinstance(value, int) and value > 0:
            limits[key] = value
        else:
            limits[key] = default

    multiplier = _normalise_multiplier(settings.get("rate_limit_multiplier", 1.0))

    update_rate_limit_state(enabled=enabled, multiplier=multiplier, limits=limits)
    return get_rate_limit_state()


__all__ = [
    "limiter",
    "SLOWAPI_AVAILABLE",
    "RATE_LIMIT_SETTING_ID",
    "RATE_LIMIT_DEFAULTS",
    "rate_limit",
    "refresh_rate_limit_config",
    "get_rate_limit_state",
    "get_effective_rate_limit",
    "is_trusted_proxy",
]
