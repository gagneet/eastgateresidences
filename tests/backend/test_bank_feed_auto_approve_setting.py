"""
Tests for the per-building bank-feed auto-approve setting (tasks/GAP-ONBOARD-001).

Covers backend/routers/bank_feeds.py::sync_demo_bank_transactions's new
pre-sync settings lookup:
  - explicit bank_feed_auto_approve=False forces disable_auto_allocation=True
  - unset / explicit True leaves the request's own value untouched (never loosens it)
  - a settings-lookup failure must not break the sync call (fail-open, log a warning)

Pure-unit tests — mocks _get_db, get_general_settings_or_default, and
run_bank_feed_sync. Patch target: "routers.bank_feeds.<name>".
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

BUILDING_ID = "UP-TEST-001"
_SYNC_USER = {"id": "user-admin", "role": "super_admin", "effective_role": "super_admin", "email": "admin@test.com"}


def _sync_payload(**overrides):
    from routers.bank_feeds import BankFeedSyncRequest
    return BankFeedSyncRequest(**overrides)


@pytest.mark.asyncio
async def test_sync_forces_manual_review_when_setting_explicitly_off():
    from routers.bank_feeds import sync_demo_bank_transactions

    with patch("routers.bank_feeds._get_db", return_value=MagicMock()), \
         patch("routers.bank_feeds.get_general_settings_or_default",
               AsyncMock(return_value={"bank_feed_auto_approve": False})), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={"processed": 0})) as mock_sync:
        await sync_demo_bank_transactions(
            payload=_sync_payload(),
            current_user=_SYNC_USER,
            building_id=BUILDING_ID,
        )

    sent_payload = mock_sync.call_args.kwargs["payload"]
    assert sent_payload.disable_auto_allocation is True


@pytest.mark.asyncio
async def test_sync_preserves_default_behaviour_when_setting_unset():
    """Unset (never configured) must NOT change sync behaviour — every building
    today has no row for this setting, and must see zero behaviour change."""
    from routers.bank_feeds import sync_demo_bank_transactions

    with patch("routers.bank_feeds._get_db", return_value=MagicMock()), \
         patch("routers.bank_feeds.get_general_settings_or_default", AsyncMock(return_value={})), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={"processed": 0})) as mock_sync:
        await sync_demo_bank_transactions(
            payload=_sync_payload(),
            current_user=_SYNC_USER,
            building_id=BUILDING_ID,
        )

    sent_payload = mock_sync.call_args.kwargs["payload"]
    assert sent_payload.disable_auto_allocation is False


@pytest.mark.asyncio
async def test_sync_preserves_default_behaviour_when_setting_explicitly_true():
    """Explicit True is a no-op vs. today's default — confirms the setting
    only ever tightens the gate, never changes behaviour when 'on'."""
    from routers.bank_feeds import sync_demo_bank_transactions

    with patch("routers.bank_feeds._get_db", return_value=MagicMock()), \
         patch("routers.bank_feeds.get_general_settings_or_default",
               AsyncMock(return_value={"bank_feed_auto_approve": True})), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={"processed": 0})) as mock_sync:
        await sync_demo_bank_transactions(
            payload=_sync_payload(),
            current_user=_SYNC_USER,
            building_id=BUILDING_ID,
        )

    sent_payload = mock_sync.call_args.kwargs["payload"]
    assert sent_payload.disable_auto_allocation is False


@pytest.mark.asyncio
async def test_sync_never_loosens_an_explicit_request_override():
    """A caller that already asked for manual-review-only (e.g. a historical
    backfill run) must stay that way regardless of the building's setting —
    the setting only ever ADDS strictness, never removes a caller's own."""
    from routers.bank_feeds import sync_demo_bank_transactions

    with patch("routers.bank_feeds._get_db", return_value=MagicMock()), \
         patch("routers.bank_feeds.get_general_settings_or_default",
               AsyncMock(return_value={"bank_feed_auto_approve": True})) as mock_settings, \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={"processed": 0})) as mock_sync:
        await sync_demo_bank_transactions(
            payload=_sync_payload(disable_auto_allocation=True),
            current_user=_SYNC_USER,
            building_id=BUILDING_ID,
        )

    # Short-circuits before even looking up the setting — request already strict.
    mock_settings.assert_not_awaited()
    sent_payload = mock_sync.call_args.kwargs["payload"]
    assert sent_payload.disable_auto_allocation is True


@pytest.mark.asyncio
async def test_sync_fails_open_when_settings_lookup_raises():
    """A Postgres/Mongo hiccup in the settings read path must not break sync —
    this endpoint had zero dependency on settings_service before this change."""
    from routers.bank_feeds import sync_demo_bank_transactions

    with patch("routers.bank_feeds._get_db", return_value=MagicMock()), \
         patch("routers.bank_feeds.get_general_settings_or_default",
               AsyncMock(side_effect=RuntimeError("db unavailable"))), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={"processed": 0})) as mock_sync:
        result = await sync_demo_bank_transactions(
            payload=_sync_payload(),
            current_user=_SYNC_USER,
            building_id=BUILDING_ID,
        )

    assert result == {"processed": 0}
    sent_payload = mock_sync.call_args.kwargs["payload"]
    assert sent_payload.disable_auto_allocation is False


@pytest.mark.asyncio
async def test_sync_rejects_unauthorized_role():
    from routers.bank_feeds import sync_demo_bank_transactions
    from fastapi import HTTPException

    tenant_user = {"id": "u2", "role": "tenant", "effective_role": "tenant"}
    with patch("routers.bank_feeds._get_db", return_value=MagicMock()), \
         pytest.raises(HTTPException) as exc:
        await sync_demo_bank_transactions(
            payload=_sync_payload(),
            current_user=tenant_user,
            building_id=BUILDING_ID,
        )
    assert exc.value.status_code == 403
