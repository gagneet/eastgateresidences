"""Security and compatibility tests for canonical request metadata."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from utils.error_response import get_request_id  # noqa: E402
from utils.geo import get_real_ip  # noqa: E402
from utils.rate_limit import _get_real_ip  # noqa: E402
from utils.request_metadata import request_audit_metadata, request_metadata  # noqa: E402


class _Request:
    def __init__(self, peer: str | None, headers: dict[str, str] | None = None):
        self.client = SimpleNamespace(host=peer) if peer else None
        self.headers = headers or {}
        self.state = SimpleNamespace()


def test_trusted_proxy_metadata_keeps_public_and_local_provenance():
    request = _Request(
        "127.0.0.1",
        {
            "X-Forwarded-For": "8.8.8.8",
            "X-Request-ID": "edge:req/123",
            "User-Agent": "Example Browser",
        },
    )

    metadata = request_metadata(request)

    assert metadata.correlation_id == "edge:req/123"
    assert metadata.ip_address == "8.8.8.8"
    assert metadata.public_ip == "8.8.8.8"
    assert metadata.local_ip == "127.0.0.1"
    assert metadata.ip_display == "8.8.8.8 (127.0.0.1)"
    assert metadata.audit_tuple() == ("edge:req/123", "8.8.8.8", "Example Browser")


def test_untrusted_peer_cannot_forge_forwarded_ip():
    metadata = request_metadata(
        _Request("9.9.9.9", {"X-Forwarded-For": "1.1.1.1"})
    )

    assert metadata.ip_address == "9.9.9.9"
    assert metadata.public_ip == "9.9.9.9"
    assert metadata.ip_address != "1.1.1.1"


def test_invalid_request_id_falls_back_to_safe_correlation_header():
    request = _Request(
        "127.0.0.1",
        {
            "X-Request-ID": "unsafe\r\nX-Injected: yes",
            "X-Correlation-ID": "safe-correlation-42",
        },
    )

    assert get_request_id(request) == "safe-correlation-42"
    assert request.state.request_id == "safe-correlation-42"


def test_missing_or_overlong_request_id_is_replaced_with_uuid():
    for headers in ({}, {"X-Request-ID": "a" * 129}):
        request_id = get_request_id(_Request("127.0.0.1", headers))
        assert str(uuid.UUID(request_id)) == request_id


def test_user_agent_is_control_character_cleaned_and_bounded():
    metadata = request_metadata(
        _Request("127.0.0.1", {"User-Agent": "Browser\r\nInjected" + "x" * 300})
    )

    assert metadata.user_agent is not None
    assert "\r" not in metadata.user_agent
    assert "\n" not in metadata.user_agent
    assert len(metadata.user_agent) == 256


def test_legacy_audit_tuple_is_non_empty_when_headers_are_missing():
    correlation_id, ip_address, user_agent = request_audit_metadata(
        _Request(None)
    )

    assert str(uuid.UUID(correlation_id)) == correlation_id
    assert ip_address == "unknown"
    assert user_agent is None


def test_legacy_ip_wrappers_share_the_canonical_policy():
    for request in (
        _Request("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"}),
        _Request("9.9.9.9", {"X-Forwarded-For": "1.1.1.1"}),
        _Request(None),
    ):
        canonical = request_metadata(request).ip_address
        assert get_real_ip(request) == canonical
        assert _get_real_ip(request) == canonical
