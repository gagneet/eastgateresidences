"""Tests for docs integrity audit script."""
# @featuretrace:docs-archive-reconciliation -- Fixture coverage for docs integrity audit behavior.
# Layer: test
# Data flow: pytest fixtures -> audit_docs_integrity.audit_docs -> docs integrity findings (global).
# Related: scripts/validation/audit_docs_integrity.py
#          docs/README.md
#          docs/doc_inventory.md
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validation" / "audit_docs_integrity.py"
SPEC = importlib.util.spec_from_file_location("audit_docs_integrity", SCRIPT_PATH)
assert SPEC and SPEC.loader
audit_docs_integrity = importlib.util.module_from_spec(SPEC)
sys.modules["audit_docs_integrity"] = audit_docs_integrity
SPEC.loader.exec_module(audit_docs_integrity)
audit_docs = audit_docs_integrity.audit_docs


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_link_fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "target.md", "# Target\n")
    _write(docs / "index.md", "# Index\n\n[Target](target.md)\n")
    assert not [f for f in audit_docs(docs) if f.code == "broken_link"]


def test_broken_link_fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "index.md", "# Index\n\n[Missing](missing.md)\n")
    findings = audit_docs(docs)
    assert any(f.code == "broken_link" for f in findings)


def test_missing_status_header_fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "architecture" / "source-of-truth-matrix.md", "# Source-of-Truth Matrix\n")
    findings = audit_docs(docs)
    assert any(f.code == "missing_status_header" for f in findings)


def test_missing_status_header_for_docs_index_files(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "README.md", "# Docs\n")
    findings = audit_docs(docs)
    assert any(f.code == "missing_status_header" and f.path == "docs/README.md" for f in findings)


def test_stale_archive_reference_fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "archive" / "old.md", "# Old\n")
    _write(
        docs / "architecture" / "source-of-truth-matrix.md",
        "# Source-of-Truth Matrix\n\nStatus: current_authoritative\nOwner: Platform engineering\nLast validated: 2026-06-06\nScope: Test\n\n[Old](../archive/old.md)\n",
    )
    findings = audit_docs(docs)
    assert any(f.code == "stale_archive_reference" for f in findings)


def test_duplicate_title_fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "a.md", "# Same\n")
    _write(docs / "b.md", "# Same\n")
    findings = audit_docs(docs)
    assert any(f.code == "duplicate_title" for f in findings)
