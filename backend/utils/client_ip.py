# @featuretrace:security-ip-logging — Resolve and classify the caller's public and local IPs.
# Layer: service
# Data flow: Request headers + TCP peer -> resolve_client_ips() -> {public_ip, local_ip}
#            -> login_audit_logs / users.last_login_* -> dashboard + /admin/security-ip-logs (global).
# Related: backend/utils/geo.py
#          backend/utils/rate_limit.py
#          backend/routers/auth.py
#          frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
# Tests: tests/backend/test_client_ip_resolution.py

"""Record BOTH the public and the local address a request arrived from.

## Why one value was not enough

``geo.get_real_ip()`` returns a single "real" IP, and every audit row and
dashboard line stored that. When it resolved to something like ``10.0.0.7`` or
``172.18.0.1`` — an internal address — there was no way to tell *which* of three
very different situations had occurred:

1. the proxy forwarded no ``X-Real-IP``/``X-Forwarded-For`` header at all;
2. the proxy's own address is missing from ``TRUSTED_PROXY_CIDRS``, so the
   headers were present but deliberately ignored as untrustworthy; or
3. the caller genuinely is on the local network.

All three produce an internal-looking IP, and the fix for each is different.
Storing both addresses makes them distinguishable at a glance, which is what the
operator actually needs from a security log.

## The two values

``local_ip``
    The address the TCP connection actually came from — ``request.client.host``.
    Behind nginx or a container network this is the proxy, not the human. It is
    never spoofable, which is what makes it useful as corroboration.

``public_ip``
    The globally-routable address believed to be the real client, taken from
    proxy headers **only when the connecting peer is a trusted proxy**. That
    trust rule is unchanged and deliberately strict: an untrusted peer that sets
    ``X-Forwarded-For`` is trying to forge its identity, and is recorded as
    itself.

Either may be ``None``. On a laptop hitting a dev server directly there is no
proxy and no public address; behind a proxy with no headers there is no public
address either. ``None`` is recorded as ``None`` rather than being backfilled
with the other value — the whole point is to tell the cases apart.

## Classification

"Public" here means **globally routable** per :mod:`ipaddress`, not "came from a
header". A proxy on a private network that forwards ``192.168.1.50`` has
forwarded a private address, and calling that a public IP would restate the very
confusion this module exists to remove.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Headers that may carry the originating client address, most specific first.
#: Only consulted when the connecting peer is a trusted proxy.
#:
#: CF-Connecting-IP leads because when Cloudflare is in front of nginx, nginx's
#: X-Real-IP is the Cloudflare edge address rather than the browser's.
_FORWARD_HEADERS = ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For")


@dataclass(frozen=True, slots=True)
class ClientIPs:
    """The addresses a request arrived from.

    ``display`` is the canonical rendering used by the UI and shared here so the
    dashboard, the security log and any export cannot format it three different
    ways.
    """

    public_ip: str | None
    local_ip: str | None

    @property
    def best(self) -> str:
        """Single best-known address, for the legacy ``ip_address`` field.

        Prefers the public address, because that is the one that identifies a
        person rather than a piece of infrastructure. Falls back to the local
        address, then to ``"unknown"`` — never to an empty string, which reads
        as "no value recorded" rather than "could not determine".
        """
        return self.public_ip or self.local_ip or "unknown"

    @property
    def display(self) -> str:
        """``"118.210.60.180 (10.0.0.7)"``, or a single address when only one is known.

        The bracketed local address is shown so an operator can confirm the
        request really did traverse the expected proxy. When both are the same
        value — a direct connection from a public address — it is shown once
        rather than duplicated.
        """
        if self.public_ip and self.local_ip and self.public_ip != self.local_ip:
            return f"{self.public_ip} ({self.local_ip})"
        return self.public_ip or self.local_ip or "unknown"


def _parse(value: str | None) -> ipaddress._BaseAddress | None:
    """Parse an address, unwrapping IPv6-mapped IPv4 so classification is consistent."""
    if not value:
        return None
    candidate = str(value).strip()
    if not candidate or candidate.lower() == "unknown":
        return None
    # Strip a port if one came along ("1.2.3.4:5678"), which some proxies append.
    if candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address


def _is_public(address: ipaddress._BaseAddress | None) -> bool:
    """True when the address is globally routable.

    ``is_global`` already excludes private, loopback, link-local, multicast and
    reserved ranges, so this is one check rather than a hand-maintained list that
    would drift from the standard.
    """
    return bool(address is not None and address.is_global)


def _forwarded_candidates(request: Any) -> list[str]:
    """Addresses the trusted proxy claims the request originated from.

    ``X-Forwarded-For`` is a chain — leftmost is the original client, later
    entries are intermediate proxies — so every hop is returned and the caller
    picks the first globally-routable one. Taking only the leftmost fails when a
    corporate proxy prepends its own private address.
    """
    candidates: list[str] = []
    for header in _FORWARD_HEADERS:
        raw = (request.headers.get(header) or "").strip()
        if not raw:
            continue
        candidates.extend(part.strip() for part in raw.split(",") if part.strip())
    return candidates


def resolve_client_ips(request: Any) -> ClientIPs:
    """Return the public and local addresses this request arrived from.

    Never raises. A resolution failure yields ``ClientIPs(None, None)``, which
    renders as ``"unknown"`` — an audit row that says "we could not tell" is
    honest, whereas one that quietly records the proxy's address as the user's
    is not.
    """
    from utils.rate_limit import is_trusted_proxy  # lazy: avoids a circular import

    try:
        peer_raw = request.client.host if getattr(request, "client", None) else None
        peer = _parse(peer_raw)

        public: ipaddress._BaseAddress | None = None
        local: ipaddress._BaseAddress | None = peer if peer is not None and not _is_public(peer) else None

        # A directly-connected client with a routable address IS the public IP.
        if _is_public(peer):
            public = peer

        # Proxy headers are consulted ONLY when the peer is a trusted proxy.
        # An untrusted peer setting X-Forwarded-For is forging its identity, and
        # is recorded as itself — this rule is inherited unchanged from
        # geo.get_real_ip and must not be relaxed here.
        if peer_raw and is_trusted_proxy(peer_raw):
            for candidate in _forwarded_candidates(request):
                parsed = _parse(candidate)
                if parsed is None:
                    continue
                if _is_public(parsed):
                    public = parsed
                    break
                # A private forwarded address is still better provenance for
                # "local" than the proxy itself: it is the actual originating
                # host on the internal network.
                if local is None or local == peer:
                    local = parsed

        return ClientIPs(
            public_ip=str(public) if public is not None else None,
            local_ip=str(local) if local is not None else None,
        )
    except Exception:  # noqa: BLE001 — IP resolution must never break a login
        logger.warning("client IP resolution failed; recording unknown", exc_info=True)
        return ClientIPs(public_ip=None, local_ip=None)


def ip_fields(request: Any) -> dict[str, str | None]:
    """Ready-to-store fields for an audit row.

    ``ip_address`` is retained alongside the new pair so existing readers,
    indexes and exports keep working unchanged; it holds the same value they
    would have seen before.
    """
    resolved = resolve_client_ips(request)
    return {
        "ip_address": resolved.best,
        "public_ip": resolved.public_ip,
        "local_ip": resolved.local_ip,
        "ip_display": resolved.display,
    }
