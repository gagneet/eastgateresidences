from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "check_alembic_head.py"
BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
SPEC = importlib.util.spec_from_file_location("check_alembic_head", MODULE_PATH)
assert SPEC and SPEC.loader
check_alembic_head = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_alembic_head
original_sys_path = sys.path[:]

# tests/backend/conftest.py inserts BACKEND_PATH at sys.path[0] for the whole
# pytest session (needed so every other test can `import services.X`/
# `routers.Y` directly) — backend/alembic/__init__.py (an empty scaffolding
# file, not meant to be importable; Alembic's own CLI loads env.py by file
# path, never via `import alembic.env`) then shadows the real `alembic` PyPI
# package the instant anything does a bare `import alembic` while
# BACKEND_PATH precedes the venv's site-packages on sys.path. Once that
# happens, sys.modules['alembic'] is permanently poisoned for the rest of the
# process — stripping BACKEND_PATH from sys.path here (as this file already
# did) does NOT fix it, because Python's import system checks sys.modules
# before consulting sys.path at all. Collection order across 8000+ tests in
# the full suite means this file cannot rely on being the first to import
# `alembic`, so it must also evict any already-cached (possibly wrong)
# `alembic`/`alembic.*` entries and force a clean re-resolution.
for _mod_name in [name for name in sys.modules if name == "alembic" or name.startswith("alembic.")]:
    del sys.modules[_mod_name]

# An exact-string match against BACKEND_PATH is not enough: pytest's own
# rootdir-relative sys.path insertion (--import-mode=prepend, one entry per
# conftest.py directory) can add an *unnormalized* equivalent such as
# ".../tests/backend/../../backend" alongside the normalized
# ".../strata-management/backend" conftest.py inserts itself — both resolve
# to the same real directory but compare unequal as strings, so the
# unnormalized one survives a plain `!= BACKEND_PATH` filter and keeps
# shadowing the real `alembic` package. Normalize every entry before
# comparing so all equivalent forms are excluded.
_normalized_backend_path = os.path.normpath(os.path.abspath(BACKEND_PATH))

try:
    sys.path = [
        entry for entry in sys.path
        if os.path.normpath(os.path.abspath(entry)) != _normalized_backend_path
    ]
    SPEC.loader.exec_module(check_alembic_head)
finally:
    sys.path = original_sys_path


def test_evaluate_heads_accepts_matching_single_head():
    result = check_alembic_head.evaluate_heads(["0036"], "0036", "ci postgres")

    assert result.database_head == "0036"
    assert "IN SYNC" in result.success_message()


def test_evaluate_heads_rejects_mismatch():
    with pytest.raises(check_alembic_head.HeadMismatchError, match="OUT OF SYNC"):
        check_alembic_head.evaluate_heads(["0032"], "0036", "production")


def test_evaluate_heads_rejects_multiple_database_heads():
    with pytest.raises(
        check_alembic_head.DatabaseVersionStateError,
        match="multiple Alembic heads",
    ):
        check_alembic_head.evaluate_heads(["0035", "0036"], "0036", "production")


def test_mask_dsn_hides_credentials():
    masked = check_alembic_head.mask_dsn(
        "postgresql+asyncpg://user:secret@db.internal:5432/strataos"
    )

    assert masked == "postgresql+asyncpg://***@db.internal:5432/strataos"
