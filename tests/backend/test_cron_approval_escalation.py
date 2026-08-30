# @featuretrace:owner-transfers — Regression test for the owner-approval escalation cron's
# admin-recipient query. Not a full end-to-end run (email sending, token generation);
# scoped to the specific bug found during the 'chairman' role-literal sweep.
# Layer: test
# Related: backend/cron/cron_approval_escalation.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_mongo_client(overdue_users, admin_users):
    mock_db = MagicMock()
    mock_db_client = MagicMock()
    mock_db_client.admin.command = AsyncMock(return_value={"ok": 1})

    overdue_cursor = MagicMock()
    overdue_cursor.to_list = AsyncMock(return_value=overdue_users)
    admin_cursor = MagicMock()
    admin_cursor.to_list = AsyncMock(return_value=admin_users)

    # First .find(...).to_list(200) call is the overdue-registrations lookup, the
    # second .find(...).to_list(50) call is the admin-recipient lookup — return them
    # in call order via side_effect.
    mock_db.users.find = MagicMock(side_effect=[overdue_cursor, admin_cursor])
    mock_db_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_db_client.close = MagicMock()

    mock_client_cls = MagicMock(return_value=mock_db_client)
    return mock_client_cls, mock_db


@pytest.mark.asyncio
async def test_admin_recipient_query_includes_ec_member_not_chairman_literal():
    """Regression: the admin-recipient query previously read
    role: {"$in": ["super_admin", "chairman", "strata_admin"]} — 'chairman' can never
    match a real user.role value (see rules/post-compact-critical.md), and 'ec_member'
    (what a real chairman's role actually is) was missing entirely, so real chairmen
    never received escalation emails. No admins are returned here (admins=[]) so the
    function returns right after the query this test inspects, without needing to mock
    email-sending or token generation."""
    import cron.cron_approval_escalation as cron_module

    overdue_users = [
        {"id": "u1", "full_name": "Overdue Guest", "email": "guest@example.com",
         "role": "guest", "unit_number": "12", "owner_notified_at": "2020-01-01T00:00:00+00:00"},
    ]
    mock_client_cls, mock_db = _mock_mongo_client(overdue_users, admin_users=[])

    with patch.object(cron_module, "AsyncMongoClient", mock_client_cls):
        await cron_module.run_escalation()

    find_calls = mock_db.users.find.call_args_list
    assert len(find_calls) == 2, "expected exactly overdue-lookup + admin-lookup queries"

    admin_query = find_calls[1][0][0]
    assert admin_query["role"]["$in"] == ["super_admin", "ec_member", "strata_admin"]
    assert "chairman" not in admin_query["role"]["$in"]
