"""
test_announcement_broadcast.py — Unit tests for the announcement broadcast feature.

Tests cover:
- POST /announcements/broadcast creates announcement in multiple buildings
- Only super_admin and strata_manager can broadcast
- Returns correct count of created announcements
- Broadcasts to all buildings when target_building_ids is None
- Broadcasts only to target buildings when specified
- Strata manager can only broadcast to their own buildings
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.communication import AnnouncementCreate, NoticeCreate


# ── helpers ──────────────────────────────────────────────────────────────────

def _close_scheduled_coroutine(coro):
    """Test scheduler stub: skip side effects, but close fire-and-forget coroutine handles."""
    if hasattr(coro, "close"):
        coro.close()
    return MagicMock()


def _make_user(role="super_admin", building_id="13195", **kwargs):
    return {
        "id": "user-001",
        "email": "admin@example.com",
        "full_name": "Test Admin",
        "role": role,
        "building_id": building_id,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
        **kwargs,
    }


def _mock_db():
    db = MagicMock()
    db.buildings.find.return_value.to_list = AsyncMock(return_value=[])
    db.memberships.find.return_value.to_list = AsyncMock(return_value=[])
    db.announcements.insert_one = AsyncMock(return_value=None)
    db.notices.insert_one = AsyncMock(return_value=None)
    db.audit_logs.insert_one = AsyncMock(return_value=None)
    return db


BUILDING_A = {"id": "13195", "name": "East Gate Residences", "is_active": True}
BUILDING_B = {"id": "18932", "name": "Harbourview Residences", "is_active": True}

ANNOUNCEMENT_DATA = AnnouncementCreate(
    title="Test Broadcast",
    content="This is a broadcast announcement.",
    priority="normal",
    is_public=True,
    expires_at=None,
    target_roles=[],
    target_users=[],
)

NOTICE_DATA = NoticeCreate(
    title="Test Notice Broadcast",
    content="This is a broadcast notice.",
    category="general",
    priority="urgent",
    is_pinned=False,
    requires_acknowledgment=True,
    expires_at=None,
    target_roles=[],
)


# ── Test: Permission enforcement ──────────────────────────────────────────

class TestBroadcastPermissions:
    """Only super_admin and strata_manager may call broadcast."""

    @pytest.mark.asyncio
    async def test_owner_cannot_broadcast(self):
        """Owner role is rejected with 403."""
        from routers.communication import broadcast_announcement
        from fastapi import HTTPException

        mock_db = _mock_db()
        user = _make_user(role="owner")

        with patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await broadcast_announcement(
                    announcement=ANNOUNCEMENT_DATA,
                    target_building_ids=None,
                    current_user=user,
                )

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_cannot_broadcast(self):
        """EC member role is rejected with 403."""
        from routers.communication import broadcast_announcement
        from fastapi import HTTPException

        mock_db = _mock_db()
        user = _make_user(role="ec_member")

        with patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await broadcast_announcement(
                    announcement=ANNOUNCEMENT_DATA,
                    target_building_ids=None,
                    current_user=user,
                )

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_chairman_cannot_broadcast(self):
        """Chairman role is also rejected — broadcast is super_admin/strata_manager only."""
        from routers.communication import broadcast_announcement
        from fastapi import HTTPException

        mock_db = _mock_db()
        user = _make_user(role="chairman")

        with patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await broadcast_announcement(
                    announcement=ANNOUNCEMENT_DATA,
                    target_building_ids=None,
                    current_user=user,
                )

        assert exc.value.status_code == 403


# ── Test: Super admin broadcast ───────────────────────────────────────────

class TestSuperAdminBroadcast:
    """Super admin can broadcast to all buildings or a subset."""

    @pytest.mark.asyncio
    async def test_super_admin_broadcasts_to_all_buildings(self):
        """Without target_building_ids, super_admin broadcasts to all active buildings."""
        from routers.communication import broadcast_announcement

        mock_db = _mock_db()
        mock_db.buildings.find.return_value.to_list = AsyncMock(
            return_value=[BUILDING_A, BUILDING_B]
        )
        inserts = []
        mock_db.announcements.insert_one = AsyncMock(side_effect=lambda doc: inserts.append(doc))
        user = _make_user(role="super_admin")

        with patch("routers.communication.db", mock_db), \
                patch("routers.communication.asyncio.create_task", side_effect=_close_scheduled_coroutine), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            result = await broadcast_announcement(
                announcement=ANNOUNCEMENT_DATA,
                target_building_ids=None,
                current_user=user,
            )

        assert result["created"] == 2
        assert len(result["buildings"]) == 2
        assert len(result["announcement_ids"]) == 2
        assert len(inserts) == 2

    @pytest.mark.asyncio
    async def test_super_admin_broadcasts_to_target_buildings(self):
        """Super admin can target a specific subset of buildings."""
        from routers.communication import broadcast_announcement

        mock_db = _mock_db()
        mock_db.buildings.find.return_value.to_list = AsyncMock(
            return_value=[BUILDING_A]
        )
        inserts = []
        mock_db.announcements.insert_one = AsyncMock(side_effect=lambda doc: inserts.append(doc))
        user = _make_user(role="super_admin")

        with patch("routers.communication.db", mock_db), \
                patch("routers.communication.asyncio.create_task", side_effect=_close_scheduled_coroutine), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            result = await broadcast_announcement(
                announcement=ANNOUNCEMENT_DATA,
                target_building_ids=["13195"],
                current_user=user,
            )

        assert result["created"] == 1
        assert result["buildings"][0]["id"] == "13195"

    @pytest.mark.asyncio
    async def test_broadcast_returns_correct_structure(self):
        """Return value has created, buildings, announcement_ids keys."""
        from routers.communication import broadcast_announcement

        mock_db = _mock_db()
        mock_db.buildings.find.return_value.to_list = AsyncMock(
            return_value=[BUILDING_A]
        )
        user = _make_user(role="super_admin")

        with patch("routers.communication.db", mock_db), \
                patch("routers.communication.asyncio.create_task", side_effect=_close_scheduled_coroutine), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            result = await broadcast_announcement(
                announcement=ANNOUNCEMENT_DATA,
                target_building_ids=None,
                current_user=user,
            )

        assert "created" in result
        assert "buildings" in result
        assert "announcement_ids" in result
        assert isinstance(result["announcement_ids"], list)

    @pytest.mark.asyncio
    async def test_broadcast_to_no_buildings_raises_404(self):
        """When no buildings match, a 404 is returned."""
        from routers.communication import broadcast_announcement
        from fastapi import HTTPException

        mock_db = _mock_db()
        mock_db.buildings.find.return_value.to_list = AsyncMock(return_value=[])
        user = _make_user(role="super_admin")

        with patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await broadcast_announcement(
                    announcement=ANNOUNCEMENT_DATA,
                    target_building_ids=None,
                    current_user=user,
                )

        assert exc.value.status_code == 404


# ── Test: Strata manager broadcast ────────────────────────────────────────

class TestStrataManagerBroadcast:
    """Strata manager can only broadcast to buildings they have membership in."""

    @pytest.mark.asyncio
    async def test_strata_manager_broadcasts_to_own_buildings(self):
        """Strata manager's broadcast is scoped to their memberships."""
        from routers.communication import broadcast_announcement

        mock_db = _mock_db()
        mock_db.memberships.find.return_value.to_list = AsyncMock(
            return_value=[{"building_id": "13195", "is_active": True}]
        )
        mock_db.buildings.find.return_value.to_list = AsyncMock(
            return_value=[BUILDING_A]
        )
        inserts = []
        mock_db.announcements.insert_one = AsyncMock(side_effect=lambda doc: inserts.append(doc))
        user = _make_user(role="strata_manager")

        with patch("routers.communication.db", mock_db), \
                patch("routers.communication.asyncio.create_task", side_effect=_close_scheduled_coroutine), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            result = await broadcast_announcement(
                announcement=ANNOUNCEMENT_DATA,
                target_building_ids=None,
                current_user=user,
            )

        assert result["created"] == 1

    @pytest.mark.asyncio
    async def test_strata_manager_cannot_broadcast_to_unaffiliated_building(self):
        """Strata manager cannot target a building they're not a member of."""
        from routers.communication import broadcast_announcement
        from fastapi import HTTPException

        mock_db = _mock_db()
        mock_db.memberships.find.return_value.to_list = AsyncMock(
            return_value=[{"building_id": "13195", "is_active": True}]
        )
        mock_db.buildings.find.return_value.to_list = AsyncMock(return_value=[])
        user = _make_user(role="strata_manager")

        with patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await broadcast_announcement(
                    announcement=ANNOUNCEMENT_DATA,
                    target_building_ids=["18932"],  # not their building
                    current_user=user,
                )

        # Filtered out → empty accessible_ids → 403
        assert exc.value.status_code in (403, 404)


# ── Test: Notice broadcast consolidation ─────────────────────────────────

class TestNoticeBroadcast:
    """Notice Board preserves the cross-building broadcast capability."""

    @pytest.mark.asyncio
    async def test_super_admin_broadcasts_notices_to_all_buildings(self):
        """Without target_building_ids, super_admin creates one notice per active building."""
        from routers.communication import broadcast_notice

        mock_db = _mock_db()
        mock_db.buildings.find.return_value.to_list = AsyncMock(
            return_value=[BUILDING_A, BUILDING_B]
        )
        inserts = []
        mock_db.notices.insert_one = AsyncMock(side_effect=lambda doc: inserts.append(doc))
        user = _make_user(role="super_admin")

        with patch("routers.communication.db", mock_db), \
                patch("routers.communication.asyncio.create_task", side_effect=_close_scheduled_coroutine), \
                patch("routers.communication.create_audit_log", new_callable=AsyncMock):
            result = await broadcast_notice(
                notice=NOTICE_DATA,
                target_building_ids=None,
                current_user=user,
            )

        assert result["created"] == 2
        assert len(result["notice_ids"]) == 2
        assert [doc["building_id"] for doc in inserts] == ["13195", "18932"]
        assert all(doc["requires_acknowledgment"] is True for doc in inserts)
        assert all(doc["history"][0]["action"] == "broadcast" for doc in inserts)

    @pytest.mark.asyncio
    async def test_owner_cannot_broadcast_notice(self):
        """Owner role is rejected from notice broadcast with 403."""
        from routers.communication import broadcast_notice
        from fastapi import HTTPException

        mock_db = _mock_db()
        user = _make_user(role="owner")

        with patch("routers.communication.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await broadcast_notice(
                    notice=NOTICE_DATA,
                    target_building_ids=None,
                    current_user=user,
                )

        assert exc.value.status_code == 403
