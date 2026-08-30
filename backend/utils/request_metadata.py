# @featuretrace:request-metadata — Canonical security-safe request provenance for audit writes.
# Layer: service
# Data flow: FastAPI Request -> trusted IP resolver + bounded headers -> audit/service calls.
# Related: backend/utils/client_ip.py
#          backend/utils/error_response.py
# Tests: tests/security/test_request_metadata.py
"""Build one typed request-metadata value for backend audit events."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Request

from utils.client_ip import ip_fields
from utils.error_response import get_request_id

_MAX_USER_AGENT_LENGTH = 256
_CONTROL_CHARACTERS_RE = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True, slots=True)
class RequestMetadata:
    """Validated provenance shared by routers and service audit calls."""

    correlation_id: str
    ip_address: str
    public_ip: str | None
    local_ip: str | None
    ip_display: str
    user_agent: str | None

    def audit_tuple(self) -> tuple[str, str, str | None]:
        """Compatibility shape used by existing service method signatures."""
        return self.correlation_id, self.ip_address, self.user_agent


def _sanitise_user_agent(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _CONTROL_CHARACTERS_RE.sub(" ", value).strip()
    return cleaned[:_MAX_USER_AGENT_LENGTH] or None


def request_metadata(request: Request) -> RequestMetadata:
    """Resolve trusted IP provenance and bounded request headers once."""
    addresses = ip_fields(request)
    return RequestMetadata(
        correlation_id=get_request_id(request),
        ip_address=str(addresses.get("ip_address") or "unknown"),
        public_ip=addresses.get("public_ip"),
        local_ip=addresses.get("local_ip"),
        ip_display=str(addresses.get("ip_display") or "unknown"),
        user_agent=_sanitise_user_agent(request.headers.get("User-Agent")),
    )


def request_audit_metadata(request: Request) -> tuple[str, str, str | None]:
    """Return legacy audit arguments while callers migrate to the typed value."""
    return request_metadata(request).audit_tuple()
