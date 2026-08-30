"""
Tests for _get_real_ip and is_trusted_proxy in utils/rate_limit.py.

Security contract being verified:
- X-Forwarded-For / X-Real-IP are ONLY trusted when the actual TCP peer
  is a known trusted proxy (loopback by default).
- An untrusted client cannot spoof its IP via proxy headers to bypass
  rate limiting.
- IPv6-mapped IPv4 loopback (::ffff:127.0.0.1) is treated as trusted,
  covering dual-stack uvicorn deployments.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

from utils.rate_limit import _get_real_ip, is_trusted_proxy


def _make_request(client_host, headers=None):
    """Return a minimal mock Starlette Request."""
    req = MagicMock()
    if client_host is None:
        req.client = None
    else:
        req.client = MagicMock()
        req.client.host = client_host
    # Assign a plain dict so .get() works correctly without MagicMock magic.
    req.headers = headers or {}
    return req


# ---------------------------------------------------------------------------
# is_trusted_proxy
# ---------------------------------------------------------------------------

class TestIsTrustedProxy:
    def test_ipv4_loopback_is_trusted(self):
        assert is_trusted_proxy("127.0.0.1") is True

    def test_ipv4_loopback_range_is_trusted(self):
        # 127.0.0.0/8 covers all 127.x.x.x addresses
        assert is_trusted_proxy("127.0.0.2") is True
        assert is_trusted_proxy("127.255.255.255") is True

    def test_ipv6_loopback_is_trusted(self):
        assert is_trusted_proxy("::1") is True

    def test_ipv6_mapped_ipv4_loopback_is_trusted(self):
        # Dual-stack uvicorn can present nginx as ::ffff:127.0.0.1.
        # Without unwrapping, addr in 127.0.0.0/8 returns False.
        assert is_trusted_proxy("::ffff:127.0.0.1") is True

    def test_public_ip_is_not_trusted(self):
        assert is_trusted_proxy("203.0.113.1") is False

    def test_private_rfc1918_not_trusted_by_default(self):
        # Private ranges are intentionally excluded from the default set.
        # Operators can add them via TRUSTED_PROXY_CIDRS if needed.
        assert is_trusted_proxy("10.0.0.1") is False
        assert is_trusted_proxy("192.168.1.1") is False
        assert is_trusted_proxy("172.16.0.1") is False

    def test_invalid_address_is_not_trusted(self):
        assert is_trusted_proxy("not-an-ip") is False
        assert is_trusted_proxy("") is False
        assert is_trusted_proxy("999.999.999.999") is False


# ---------------------------------------------------------------------------
# _get_real_ip — trusted proxy path
# ---------------------------------------------------------------------------

class TestGetRealIpTrustedProxy:
    def test_prefers_x_real_ip_over_forwarded_for(self):
        # Both header values must be GLOBALLY ROUTABLE for this test to mean what
        # its name says. 203.0.113.0/24 is RFC 5737 TEST-NET-3, which Python's
        # ipaddress module reports as is_private=True / is_global=False, so the
        # resolver skips it as a non-public candidate and falls through to the
        # X-Forwarded-For value. The original fixture put documentation space in
        # the header it expected to WIN and real space in the one it expected to
        # LOSE, so it asserted the opposite of the precedence it was checking.
        # _FORWARD_HEADERS is ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        # X-Real-IP does outrank X-Forwarded-For, and with two public candidates
        # that is what this now proves.
        req = _make_request(
            "127.0.0.1",
            {"X-Real-IP": "5.6.7.8", "X-Forwarded-For": "1.2.3.4"},
        )
        assert _get_real_ip(req) == "5.6.7.8"

    def test_falls_back_to_forwarded_for_when_no_x_real_ip(self):
        req = _make_request(
            "127.0.0.1",
            {"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
        )
        assert _get_real_ip(req) == "203.0.113.5"

    def test_forwarded_for_uses_leftmost_hop(self):
        req = _make_request(
            "127.0.0.1",
            {"X-Forwarded-For": "203.0.113.5, 10.0.0.2, 172.16.0.1"},
        )
        assert _get_real_ip(req) == "203.0.113.5"

    def test_x_real_ip_whitespace_stripped(self):
        req = _make_request("127.0.0.1", {"X-Real-IP": "  203.0.113.5  "})
        assert _get_real_ip(req) == "203.0.113.5"

    def test_no_proxy_headers_returns_client_host(self):
        req = _make_request("127.0.0.1", {})
        assert _get_real_ip(req) == "127.0.0.1"

    def test_ipv6_mapped_loopback_peer_trusts_x_real_ip(self):
        # Dual-stack uvicorn: nginx peer appears as ::ffff:127.0.0.1
        req = _make_request("::ffff:127.0.0.1", {"X-Real-IP": "203.0.113.5"})
        assert _get_real_ip(req) == "203.0.113.5"

    def test_ipv6_loopback_peer_trusts_x_real_ip(self):
        req = _make_request("::1", {"X-Real-IP": "203.0.113.5"})
        assert _get_real_ip(req) == "203.0.113.5"


# ---------------------------------------------------------------------------
# _get_real_ip — untrusted client path (core security assertions)
# ---------------------------------------------------------------------------

class TestGetRealIpUntrustedClient:
    def test_ignores_x_forwarded_for_from_untrusted_client(self):
        """Core security: an attacker cannot spoof IP to bypass rate limits."""
        req = _make_request(
            "203.0.113.99",
            {"X-Forwarded-For": "127.0.0.1"},
        )
        assert _get_real_ip(req) == "203.0.113.99"

    def test_ignores_x_real_ip_from_untrusted_client(self):
        req = _make_request(
            "203.0.113.99",
            {"X-Real-IP": "1.2.3.4"},
        )
        assert _get_real_ip(req) == "203.0.113.99"

    def test_both_headers_present_but_client_untrusted(self):
        req = _make_request(
            "198.51.100.1",
            {"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "8.8.8.8"},
        )
        assert _get_real_ip(req) == "198.51.100.1"

    def test_no_proxy_headers_untrusted_client_returns_client_host(self):
        req = _make_request("203.0.113.99", {})
        assert _get_real_ip(req) == "203.0.113.99"


# ---------------------------------------------------------------------------
# _get_real_ip — edge / failure cases
# ---------------------------------------------------------------------------

class TestGetRealIpEdgeCases:
    def test_no_client_with_no_headers_is_unknown_not_a_guess(self):
        # The pre-consolidation wrapper returned "127.0.0.1" here, which asserts
        # the request came from localhost when in fact nothing is known about it.
        # utils.client_ip renders an unresolvable request as "unknown" on purpose
        # ("an audit row that says we could not tell" — resolve_client_ips), and
        # the sibling test_exception_returns_unknown already pinned that contract
        # for the failure path. As a SlowAPI key it behaves identically to the old
        # constant, except it no longer collides with genuine loopback traffic.
        req = _make_request(None, {})
        assert _get_real_ip(req) == "unknown"

    def test_exception_returns_unknown(self):
        req = MagicMock()
        type(req.client).host = property(lambda self: (_ for _ in ()).throw(Exception("boom")))
        result = _get_real_ip(req)
        assert result == "unknown"
