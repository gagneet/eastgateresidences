# @featuretrace:cutover-toggle-safety — is_test_data backstop for production writers.
# Layer: test
# Data flow: utils.test_data_flag.under_pytest -> is_test_data on shadow_diffs / core.users (global).
# Related: backend/utils/test_data_flag.py
#          backend/services/cutover_status_service.py
"""The flag both the conftest sweep and the production login gate key off.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_test_data_flag.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from utils.test_data_flag import under_pytest  # noqa: E402


def test_true_while_a_test_is_running():
    assert under_pytest() is True


def test_false_in_a_process_with_no_pytest_marker():
    with patch.dict(os.environ, {}, clear=True):
        assert under_pytest() is False


def test_identity_repo_forwards_rather_than_reimplementing():
    """A second copy of this check drifts silently — see canonical_owners.yaml."""
    from db_postgres.repos import identity_repo

    assert identity_repo._under_pytest() is under_pytest()


def test_shadow_diff_writer_imports_the_canonical_helper():
    """record_shadow_diff must OR this in; unflagged fixture rows blocked a live gate."""
    from services import cutover_status_service

    assert cutover_status_service.under_pytest is under_pytest
    source = Path(cutover_status_service.__file__).read_text()
    assert "is_test_data = bool(is_test_data) or under_pytest()" in source
