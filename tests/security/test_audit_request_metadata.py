"""Tests for the zero-baseline request-metadata ratchet."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "validation"))

from audit_request_metadata import find_violations  # noqa: E402


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_direct_forwarded_header_read_is_rejected(tmp_path):
    _write(
        tmp_path,
        "backend/routers/example.py",
        'def read(request):\n    return request.headers.get("X-Forwarded-For")\n',
    )

    findings = find_violations(tmp_path)

    assert len(findings) == 1
    assert "X-Forwarded-For" in findings[0]


def test_canonical_reader_and_shared_helper_usage_are_allowed(tmp_path):
    _write(
        tmp_path,
        "backend/utils/client_ip.py",
        'def read(request):\n    return request.headers.get("X-Real-IP")\n',
    )
    _write(
        tmp_path,
        "backend/routers/example.py",
        "from utils.request_metadata import request_metadata\n",
    )

    assert find_violations(tmp_path) == []


def test_router_local_request_meta_is_rejected(tmp_path):
    _write(
        tmp_path,
        "backend/routers/example.py",
        "def _request_meta(request):\n    return None\n",
    )

    findings = find_violations(tmp_path)

    assert len(findings) == 1
    assert "duplicates the canonical helper" in findings[0]
