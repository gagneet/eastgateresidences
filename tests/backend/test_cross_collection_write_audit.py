"""
Regression tests — Cross-Collection Write-Path Audit (Component 4).

Session: 2026-05-05 / audit/cross-collection-write-path-audit

Asserts that every HIGH-severity write site identified in the cluster audit
produces an audit_log entry after the primary write succeeds.
"""

import asyncio
import os
import sys
import uuid
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import BackgroundTasks

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret")

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).isoformat()


def _make_admin(role="super_admin"):
    return {
        "id": "admin-001", "email": "admin@test.com", "full_name": "Test Admin",
        "role": role, "effective_role": role,
        "is_active": True, "is_approved": True, "building_id": "13195",
        "unit_number": "38",
    }


def _make_invoice(inv_id="inv-001", status="pending"):
    return {
        "id": inv_id, "building_id": "13195", "invoice_number": "INV-001",
        "contractor_id": "con-001", "contractor_name": "ABC Plumbing",
        "total_amount": 1500.0, "status": status,
        "approval_status": status, "created_at": _now(),
    }


def _make_meeting(mid="mtg-001"):
    return {
        "id": mid, "building_id": "13195", "title": "EC Meeting",
        "meeting_date": "2026-06-01", "location": "Online",
        "agenda": [], "attendees": [],
        "status": "scheduled", "minutes": None,
        "created_by": "admin-001",
        "created_at": _now(), "updated_at": _now(),
    }


def _make_agm(agm_id="agm-001"):
    return {
        "id": agm_id, "building_id": "13195", "title": "2026 AGM",
        "date": "2026-07-01", "location": "Main Hall",
        "agenda": [], "status": "upcoming", "created_at": _now(),
    }


def _make_motion(motion_id="mot-001", agm_id="agm-001"):
    return {
        "id": motion_id, "building_id": "13195", "agm_id": agm_id,
        "title": "Approve Budget", "motion_type": "ordinary",
        "votes_for": 0, "votes_against": 0, "votes_abstain": 0,
        "status": "pending", "voters": [], "created_at": _now(),
    }


def _make_doc(doc_id="doc-001"):
    return {
        "id": doc_id, "building_id": "13195", "title": "Insurance Certificate",
        "category": "insurance", "uploaded_by": "admin-001",
        "is_public": False, "created_at": _now(),
    }


def _make_db():
    db = MagicMock()
    db.audit_logs.insert_one = AsyncMock()
    return db


def _agg_cursor(items):
    """Return a mock aggregate cursor with a working to_list()."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


def _async_iter(items):
    """Return a mock that supports async-for iteration."""
    async def _ait():
        for item in items:
            yield item
    m = MagicMock()
    m.__aiter__ = lambda self: _ait().__aiter__()
    return m


# ── Maintenance — invoice approve/reject/pay ──────────────────────────────────

class TestInvoiceApproveAuditLog(unittest.IsolatedAsyncioTestCase):
    """Invoice state transitions must produce audit_log entries."""

    def _mock_fetch_context(self, inv):
        """Patch _fetch_invoice_with_context to return a known invoice."""
        return patch(
            "routers.maintenance._fetch_invoice_with_context",
            new=AsyncMock(return_value=(inv, {}, {}, {}))
        )

    async def test_approve_invoice_writes_audit_log(self):
        inv = _make_invoice()
        mock_db = _make_db()
        mock_db.invoices.update_one = AsyncMock(
            return_value=MagicMock(modified_count=1)
        )

        with self._mock_fetch_context(inv), \
             patch("routers.maintenance.db", mock_db), \
             patch("routers.maintenance.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.maintenance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            from routers.maintenance import approve_invoice
            await approve_invoice(
                inv_id="inv-001",
                background_tasks=BackgroundTasks(),
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "approved")
        self.assertEqual(call_kwargs.get("resource_type"), "invoice")
        self.assertEqual(call_kwargs.get("building_id"), "13195")

    async def test_reject_invoice_writes_audit_log(self):
        inv = _make_invoice()
        mock_db = _make_db()
        mock_db.invoices.update_one = AsyncMock(
            return_value=MagicMock(modified_count=1)
        )

        with self._mock_fetch_context(inv), \
             patch("routers.maintenance.db", mock_db), \
             patch("routers.maintenance.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.maintenance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            from routers.maintenance import reject_invoice
            await reject_invoice(
                inv_id="inv-001",
                reason="Duplicate — already paid",
                background_tasks=BackgroundTasks(),
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "rejected")
        self.assertEqual(call_kwargs.get("resource_type"), "invoice")
        self.assertIn("reason", call_kwargs.get("details", {}))

    async def test_pay_invoice_writes_audit_log(self):
        inv = _make_invoice(status="approved")
        mock_db = _make_db()
        mock_db.invoices.find_one = AsyncMock(return_value=inv)
        mock_db.invoices.update_one = AsyncMock()
        mock_db.contractors.update_one = AsyncMock()
        mock_db.expense_transactions.insert_one = AsyncMock()

        with patch("routers.maintenance.db", mock_db), \
             patch("routers.maintenance.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.maintenance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            from routers.maintenance import mark_invoice_paid
            await mark_invoice_paid(
                inv_id="inv-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "paid")
        self.assertEqual(call_kwargs.get("resource_type"), "invoice")
        self.assertEqual(call_kwargs.get("building_id"), "13195")

    async def test_pay_invoice_audit_log_includes_contractor(self):
        """Audit details must include contractor name for expense traceability."""
        inv = _make_invoice(status="approved")
        mock_db = _make_db()
        mock_db.invoices.find_one = AsyncMock(return_value=inv)
        mock_db.invoices.update_one = AsyncMock()
        mock_db.contractors.update_one = AsyncMock()
        mock_db.expense_transactions.insert_one = AsyncMock()

        captured = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs.get("details", {}))

        with patch("routers.maintenance.db", mock_db), \
             patch("routers.maintenance.create_audit_log", side_effect=_capture), \
             patch("routers.maintenance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            from routers.maintenance import mark_invoice_paid
            await mark_invoice_paid(
                inv_id="inv-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        self.assertEqual(captured.get("contractor"), "ABC Plumbing")


# ── Meetings — create / update ────────────────────────────────────────────────

class TestMeetingAuditLog(unittest.IsolatedAsyncioTestCase):

    async def test_create_meeting_writes_audit_log(self):
        mock_db = _make_db()
        mock_db.meetings.insert_one = AsyncMock()

        from models.community import MeetingCreate
        meeting_data = MeetingCreate(
            title="EC Meeting", meeting_date="2026-06-01",
            location="Online", agenda=[], attendees=[],
        )

        with patch("routers.meetings.db", mock_db), \
             patch("routers.meetings.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.meetings.broadcast_user_notification", new_callable=AsyncMock), \
             patch("routers.meetings.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_meetings=True)
            from routers.meetings import create_meeting
            await create_meeting(
                meeting=meeting_data,
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "created")
        self.assertEqual(call_kwargs.get("resource_type"), "meeting")

    async def test_update_meeting_writes_audit_log(self):
        mtg = _make_meeting()
        mock_db = _make_db()
        mock_db.meetings.update_one = AsyncMock()
        mock_db.meetings.find_one = AsyncMock(return_value=mtg)

        with patch("routers.meetings.db", mock_db), \
             patch("routers.meetings.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.meetings.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_meetings=True)
            from routers.meetings import update_meeting
            await update_meeting(
                meeting_id="mtg-001",
                minutes="Minutes approved.",
                status="completed",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "updated")
        self.assertEqual(call_kwargs.get("resource_type"), "meeting")


# ── AGM — create agm / create motion / cast vote ─────────────────────────────

class TestAGMAuditLog(unittest.IsolatedAsyncioTestCase):

    async def test_create_agm_writes_audit_log(self):
        mock_db = _make_db()
        mock_db.agm.insert_one = AsyncMock()

        from models.community import AGMCreate
        agm_data = AGMCreate(
            title="2026 AGM", date="2026-07-01",
            location="Main Hall", agenda=[],
        )

        with patch("routers.meetings.db", mock_db), \
             patch("routers.meetings.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.meetings.broadcast_user_notification", new_callable=AsyncMock), \
             patch("routers.meetings.log_activity", new_callable=AsyncMock), \
             patch("routers.meetings.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_meetings=True)
            from routers.meetings import create_agm
            await create_agm(
                data=agm_data,
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "created")
        self.assertEqual(call_kwargs.get("resource_type"), "agm")

    async def test_create_motion_writes_audit_log(self):
        mock_db = _make_db()
        mock_db.agm_motions.insert_one = AsyncMock()

        from models.community import AGMMotionCreate
        motion_data = AGMMotionCreate(
            title="Approve 2026 Budget",
            description="Ordinary resolution to approve admin and sinking fund budgets.",
            motion_type="ordinary",
            agm_id="agm-001",
        )

        with patch("routers.meetings.db", mock_db), \
             patch("routers.meetings.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.meetings.log_activity", new_callable=AsyncMock), \
             patch("routers.meetings.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_meetings=True)
            from routers.meetings import create_motion
            await create_motion(
                agm_id="agm-001",
                data=motion_data,
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "motion_created")
        self.assertEqual(call_kwargs.get("resource_type"), "agm_motion")

    async def test_cast_vote_writes_audit_log(self):
        motion = _make_motion()
        agm = {**_make_agm(), "status": "in_progress"}  # must be in_progress for live vote
        mock_db = _make_db()
        mock_db.agm_motions.find_one = AsyncMock(return_value=motion)
        mock_db.agm.find_one = AsyncMock(return_value=agm)
        # Supervisor bypasses unit ownership checks
        mock_db.agm_votes.update_one = AsyncMock(
            return_value=MagicMock(upserted_id="new-vote-id")
        )
        mock_db.agm_motions.update_one = AsyncMock()

        from models.community import AGMVoteCreate
        # motion_id is required by AGMVoteCreate
        vote_data = AGMVoteCreate(motion_id="mot-001", vote="for",
                                  proxy_for=None, is_pre_vote=False)

        with patch("routers.meetings.db", mock_db), \
             patch("routers.meetings.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.meetings.effective_role", return_value="super_admin"):
            from routers.meetings import cast_vote
            await cast_vote(
                motion_id="mot-001",
                data=vote_data,
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "vote_cast")
        self.assertEqual(call_kwargs.get("resource_type"), "agm_vote")


# ── Voting — proxy submit / revoke / close-ballot ────────────────────────────

class TestVotingAuditLog(unittest.IsolatedAsyncioTestCase):

    async def test_submit_proxy_writes_audit_log(self):
        agm = _make_agm()
        mock_db = _make_db()
        mock_db.agm.find_one = AsyncMock(return_value=agm)
        mock_db.agm_attendance.update_one = AsyncMock()
        mock_db.buildings.find_one = AsyncMock(
            return_value={"jurisdiction": "ACT"}
        )

        with patch("routers.voting.db", mock_db), \
             patch("routers.voting.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.voting.log_activity", new_callable=AsyncMock), \
             patch("routers.voting.validate_proxy_eligibility",
                   new=AsyncMock(return_value={"valid": True, "reason": ""})):
            from routers.voting import submit_proxy, ProxySubmitRequest
            payload = ProxySubmitRequest(
                proxy_holder_id="holder-001",
                proxy_form_text="I, Test Admin, appoint holder-001 as my proxy for the 2026 AGM.",
            )
            await submit_proxy(
                agm_id="agm-001",
                data=payload,
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "proxy_submitted")
        self.assertIn("proxy_holder_id", call_kwargs.get("details", {}))

    async def test_revoke_proxy_writes_audit_log(self):
        agm = _make_agm()
        mock_db = _make_db()
        mock_db.agm.find_one = AsyncMock(return_value=agm)
        mock_db.agm_votes.find_one = AsyncMock(return_value=None)
        mock_db.agm_attendance.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )

        with patch("routers.voting.db", mock_db), \
             patch("routers.voting.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.voting.log_activity", new_callable=AsyncMock):
            from routers.voting import revoke_proxy
            await revoke_proxy(
                agm_id="agm-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "proxy_revoked")

    async def test_close_ballot_writes_audit_log(self):
        agm = {**_make_agm(), "status": "in_progress"}
        mock_db = _make_db()
        mock_db.agm.find_one = AsyncMock(return_value=agm)
        mock_db.agm.update_one = AsyncMock()
        mock_db.ballot_seals.insert_one = AsyncMock()
        mock_db.ballot_entries.insert_many = AsyncMock()
        mock_db.ballot_entries.count_documents = AsyncMock(return_value=0)

        # agm_votes.find is iterated directly; ballot_entries.find is chained
        # with .sort("created_at", 1) before the async-for, so the cursor mock
        # must have sort() return the async-iterable.
        mock_db.agm_votes.find.return_value = _async_iter([])
        _ballot_cursor = MagicMock()
        _ballot_cursor.sort = MagicMock(return_value=_async_iter([]))
        mock_db.ballot_entries.find.return_value = _ballot_cursor
        mock_db.ballot_entries.find_one = AsyncMock(return_value=None)
        mock_db.units.aggregate = MagicMock(return_value=_async_iter([]))

        with patch("routers.voting.db", mock_db), \
             patch("routers.voting.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.voting.log_activity", new_callable=AsyncMock), \
             patch("routers.voting.effective_role", return_value="strata_manager"):
            from routers.voting import close_ballot
            await close_ballot(
                agm_id="agm-001",
                current_user=_make_admin("strata_manager"),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "ballot_closed")
        self.assertEqual(call_kwargs.get("resource_type"), "agm")


# ── Documents — upload / delete ───────────────────────────────────────────────

class TestDocumentAuditLog(unittest.IsolatedAsyncioTestCase):

    async def test_upload_document_writes_audit_log(self):
        mock_db = _make_db()
        mock_db.documents.insert_one = AsyncMock()

        from fastapi import UploadFile
        from io import BytesIO
        upload = UploadFile(
            filename="test.pdf", file=BytesIO(b"pdf-content"),
            headers={"content-type": "application/pdf"},
        )

        with patch("routers.documents.db", mock_db), \
             patch("routers.documents.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.documents.log_activity", new_callable=AsyncMock), \
             patch("routers.documents.scan_upload", new_callable=AsyncMock), \
             patch("routers.documents.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_upload_documents=True)
            from routers.documents import upload_document
            await upload_document(
                title="Insurance Certificate",
                description="2026 cover",
                category="insurance",
                is_public=False,
                allowed_roles="[]",
                file=upload,
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "uploaded")
        self.assertEqual(call_kwargs.get("resource_type"), "document")
        self.assertEqual(call_kwargs.get("building_id"), "13195")

    async def test_delete_document_writes_audit_log(self):
        doc = _make_doc()
        mock_db = _make_db()
        mock_db.documents.find_one = AsyncMock(return_value=doc)
        mock_db.documents.delete_one = AsyncMock()

        with patch("routers.documents.db", mock_db), \
             patch("routers.documents.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.documents.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_users=True)
            from routers.documents import delete_document
            await delete_document(
                doc_id="doc-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "deleted")
        self.assertEqual(call_kwargs.get("resource_type"), "document")

    async def test_delete_document_captures_title_before_deletion(self):
        """Audit details must include title captured from the doc before delete_one fires."""
        doc = _make_doc()
        mock_db = _make_db()
        mock_db.documents.find_one = AsyncMock(return_value=doc)
        mock_db.documents.delete_one = AsyncMock()

        captured = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs.get("details", {}))

        with patch("routers.documents.db", mock_db), \
             patch("routers.documents.create_audit_log", side_effect=_capture), \
             patch("routers.documents.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_users=True)
            from routers.documents import delete_document
            await delete_document(
                doc_id="doc-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        self.assertEqual(captured.get("title"), "Insurance Certificate")
        self.assertEqual(captured.get("category"), "insurance")


# ── Essential services — update ───────────────────────────────────────────────

class TestEssentialServiceAuditLog(unittest.IsolatedAsyncioTestCase):

    async def test_update_service_entry_writes_audit_log(self):
        from bson import ObjectId
        oid = ObjectId()
        mock_db = _make_db()
        mock_db.essential_services_log.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )

        from routers.essential_services import EssentialServiceUpdate
        payload = EssentialServiceUpdate(
            status="compliant",
            certificate_url="https://example.com/cert.pdf",
        )

        with patch("routers.essential_services._get_db", return_value=mock_db), \
             patch("routers.essential_services.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.essential_services._require_manager", return_value=None):
            from routers.essential_services import update_service_entry
            await update_service_entry(
                entry_id=str(oid),
                payload=payload,
                building_id="13195",
                current_user=_make_admin(),
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "essential_service_updated")
        self.assertEqual(call_kwargs.get("resource_type"), "essential_services_log")

    async def test_update_service_entry_audit_log_excludes_internal_fields(self):
        """Audit details must not expose updated_at / updated_by internal fields."""
        from bson import ObjectId
        oid = ObjectId()
        mock_db = _make_db()
        mock_db.essential_services_log.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )

        from routers.essential_services import EssentialServiceUpdate
        payload = EssentialServiceUpdate(status="non_compliant")

        captured = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs.get("details", {}))

        with patch("routers.essential_services._get_db", return_value=mock_db), \
             patch("routers.essential_services.create_audit_log", side_effect=_capture), \
             patch("routers.essential_services._require_manager", return_value=None):
            from routers.essential_services import update_service_entry
            await update_service_entry(
                entry_id=str(oid),
                payload=payload,
                building_id="13195",
                current_user=_make_admin(),
            )
            await asyncio.sleep(0)

        self.assertNotIn("updated_at", captured)
        self.assertNotIn("updated_by", captured)
        self.assertIn("status", captured)


# ── Communication — announcement delete ──────────────────────────────────────

class TestCommunicationAuditLog(unittest.IsolatedAsyncioTestCase):

    async def test_delete_announcement_writes_audit_log(self):
        ann = {"id": "ann-001", "title": "Lift Maintenance", "building_id": "13195"}
        mock_db = _make_db()
        mock_db.announcements.find_one = AsyncMock(return_value=ann)
        mock_db.announcements.delete_one = AsyncMock()

        with patch("routers.communication.db", mock_db), \
             patch("routers.communication.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.communication.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_post_announcements=True)
            from routers.communication import delete_announcement
            await delete_announcement(
                ann_id="ann-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        self.assertEqual(call_kwargs.get("action"), "deleted")
        self.assertEqual(call_kwargs.get("resource_type"), "announcement")

    async def test_delete_announcement_captures_title_before_deletion(self):
        """Title from pre-delete fetch must appear in audit_log details."""
        ann = {"id": "ann-001", "title": "Pool Closure Notice", "building_id": "13195"}
        mock_db = _make_db()
        mock_db.announcements.find_one = AsyncMock(return_value=ann)
        mock_db.announcements.delete_one = AsyncMock()

        captured = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs.get("details", {}))

        with patch("routers.communication.db", mock_db), \
             patch("routers.communication.create_audit_log", side_effect=_capture), \
             patch("routers.communication.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_post_announcements=True)
            from routers.communication import delete_announcement
            await delete_announcement(
                ann_id="ann-001",
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        self.assertEqual(captured.get("title"), "Pool Closure Notice")

    async def test_delete_nonexistent_announcement_returns_404(self):
        """Deletion of an unknown announcement must 404, not produce an audit_log."""
        mock_db = _make_db()
        mock_db.announcements.find_one = AsyncMock(return_value=None)

        from fastapi import HTTPException
        with patch("routers.communication.db", mock_db), \
             patch("routers.communication.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.communication.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_post_announcements=True)
            from routers.communication import delete_announcement
            with self.assertRaises(HTTPException) as ctx:
                await delete_announcement(
                    ann_id="nonexistent",
                    current_user=_make_admin(),
                    building_id="13195",
                )

        self.assertEqual(ctx.exception.status_code, 404)
        mock_audit.assert_not_called()


# ── Compliance — inspection result update (WPA-016) ──────────────────────────

class TestComplianceRegisterItemAuditLog(unittest.IsolatedAsyncioTestCase):
    """Inspection result updates must produce audit_log entries (AS 1851 / WHS Act)."""

    def _make_item(self, item_id_str, result=None, responsible_id="user-resp-001"):
        return {
            "_id": item_id_str,
            "id": item_id_str,
            "register_id": "reg-001",
            "building_id": "13195",
            "inspection_result": result,
            "responsible_person_id": responsible_id,
        }

    def _patch(self, mock_db, mock_audit=None, mock_notify=None):
        from unittest.mock import patch as _patch
        patches = [
            _patch("routers.compliance_registers._get_db", return_value=mock_db),
            _patch("routers.compliance_registers.create_audit_log",
                   new_callable=AsyncMock if mock_audit is None else None,
                   side_effect=mock_audit),
            _patch("routers.compliance_registers.create_user_notification",
                   new_callable=AsyncMock if mock_notify is None else None,
                   side_effect=mock_notify),
            _patch("routers.compliance_registers._require_manager", return_value=_make_admin()),
        ]
        return patches

    async def test_update_item_writes_audit_log(self):
        from bson import ObjectId
        item_id = str(ObjectId())
        mock_db = _make_db()
        mock_db.compliance_register_items.find_one = AsyncMock(return_value=self._make_item(item_id))
        mock_db.compliance_register_items.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        mock_db.compliance_events.update_many = AsyncMock()

        with patch("routers.compliance_registers._get_db", return_value=mock_db), \
             patch("routers.compliance_registers.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.compliance_registers.create_user_notification", new_callable=AsyncMock), \
             patch("routers.compliance_registers._require_manager", return_value=_make_admin()):
            from routers.compliance_registers import update_register_item
            await update_register_item(
                register_id="reg-001", item_id=item_id,
                building_id="13195",
                payload={"inspection_result": "pass", "next_inspection_date": "2027-01-01"},
                current_user=_make_admin(),
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        kw = mock_audit.call_args.kwargs
        self.assertEqual(kw.get("action"), "item_updated")
        self.assertEqual(kw.get("resource_type"), "compliance_register_item")
        self.assertEqual(kw.get("building_id"), "13195")

    async def test_audit_log_captures_result_diff(self):
        """Old and new inspection_result must both appear in audit details."""
        from bson import ObjectId
        item_id = str(ObjectId())
        mock_db = _make_db()
        mock_db.compliance_register_items.find_one = AsyncMock(
            return_value=self._make_item(item_id, result="pass")
        )
        mock_db.compliance_register_items.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        captured = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs)

        with patch("routers.compliance_registers._get_db", return_value=mock_db), \
             patch("routers.compliance_registers.create_audit_log", side_effect=_capture), \
             patch("routers.compliance_registers.create_user_notification", new_callable=AsyncMock), \
             patch("routers.compliance_registers._require_manager", return_value=_make_admin()):
            from routers.compliance_registers import update_register_item
            await update_register_item(
                register_id="reg-001", item_id=item_id,
                building_id="13195",
                payload={"inspection_result": "fail"},
                current_user=_make_admin(),
            )
            await asyncio.sleep(0)

        details = captured.get("details", {})
        self.assertEqual(details.get("old_inspection_result"), "pass")
        self.assertEqual(details.get("new_inspection_result"), "fail")

    async def test_fail_result_notifies_responsible_person(self):
        """FAIL result must fire a user_notification to the responsible_person_id."""
        from bson import ObjectId
        item_id = str(ObjectId())
        mock_db = _make_db()
        mock_db.compliance_register_items.find_one = AsyncMock(return_value=self._make_item(item_id))
        mock_db.compliance_register_items.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        with patch("routers.compliance_registers._get_db", return_value=mock_db), \
             patch("routers.compliance_registers.create_audit_log", new_callable=AsyncMock), \
             patch("routers.compliance_registers.create_user_notification", new_callable=AsyncMock) as mock_notify, \
             patch("routers.compliance_registers._require_manager", return_value=_make_admin()):
            from routers.compliance_registers import update_register_item
            await update_register_item(
                register_id="reg-001", item_id=item_id,
                building_id="13195",
                payload={"inspection_result": "fail"},
                current_user=_make_admin(),
            )
            await asyncio.sleep(0)

        mock_notify.assert_called_once()
        kw = mock_notify.call_args.kwargs
        self.assertEqual(kw.get("user_id"), "user-resp-001")
        self.assertEqual(kw.get("building_id"), "13195")

    async def test_pass_result_does_not_notify(self):
        """PASS result must NOT fire a user_notification."""
        from bson import ObjectId
        item_id = str(ObjectId())
        mock_db = _make_db()
        mock_db.compliance_register_items.find_one = AsyncMock(return_value=self._make_item(item_id))
        mock_db.compliance_register_items.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

        with patch("routers.compliance_registers._get_db", return_value=mock_db), \
             patch("routers.compliance_registers.create_audit_log", new_callable=AsyncMock), \
             patch("routers.compliance_registers.create_user_notification", new_callable=AsyncMock) as mock_notify, \
             patch("routers.compliance_registers._require_manager", return_value=_make_admin()):
            from routers.compliance_registers import update_register_item
            await update_register_item(
                register_id="reg-001", item_id=item_id,
                building_id="13195",
                payload={"inspection_result": "pass"},
                current_user=_make_admin(),
            )
            await asyncio.sleep(0)

        mock_notify.assert_not_called()


# ── Work orders — EC approval submission (WPA-017) ────────────────────────────

class TestWorkOrderApprovalSubmitAuditLog(unittest.IsolatedAsyncioTestCase):
    """submit_approval must produce an audit_log entry on every EC vote."""

    def _approval_data(self):
        from models.work_order import WorkOrderApprovalCreate
        return WorkOrderApprovalCreate(
            work_order_id="wo-001",
            decision="approve",
            comment="Approved — within budget",
        )

    async def test_submit_approval_writes_audit_log(self):
        mock_db = _make_db()
        mock_db.work_order_approvals.insert_one = AsyncMock()

        with patch("routers.work_orders.db", mock_db), \
             patch("routers.work_orders.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.work_orders.create_user_notification", new_callable=AsyncMock), \
             patch("routers.work_orders._check_approval_completion", new_callable=AsyncMock):
            from routers.work_orders import submit_approval
            await submit_approval(
                wo_id="wo-001",
                data=self._approval_data(),
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        kw = mock_audit.call_args.kwargs
        self.assertEqual(kw.get("action"), "approval_submitted")
        self.assertEqual(kw.get("resource_type"), "work_order")
        self.assertEqual(kw.get("resource_id"), "wo-001")
        self.assertEqual(kw.get("building_id"), "13195")

    async def test_submit_approval_audit_log_includes_decision(self):
        """Audit details must carry decision and approval_id for governance trail."""
        mock_db = _make_db()
        mock_db.work_order_approvals.insert_one = AsyncMock()
        captured = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs)

        with patch("routers.work_orders.db", mock_db), \
             patch("routers.work_orders.create_audit_log", side_effect=_capture), \
             patch("routers.work_orders.create_user_notification", new_callable=AsyncMock), \
             patch("routers.work_orders._check_approval_completion", new_callable=AsyncMock):
            from routers.work_orders import submit_approval
            await submit_approval(
                wo_id="wo-001",
                data=self._approval_data(),
                current_user=_make_admin(),
                building_id="13195",
            )
            await asyncio.sleep(0)

        details = captured.get("details", {})
        self.assertIn("decision", details)
        self.assertIn("approval_id", details)


# ── Work orders — approval completion (WPA-018) ───────────────────────────────

class TestWorkOrderApprovalCompletionAuditLog(unittest.IsolatedAsyncioTestCase):
    """_check_approval_completion must audit + notify requester on both outcomes."""

    def _make_wo(self, wo_id="wo-001"):
        return {
            "id": wo_id, "building_id": "13195",
            "title": "Emergency Pump Repair",
            "estimated_cost": 3500.0,
            "created_by": "owner-user-001",
        }

    def _make_approval_doc(self, decision="approve", ec_position=None):
        return {
            "approver_id": "ec-001",
            "approver_name": "EC Chair",
            "role": "ec_member",
            "ec_position": ec_position,
            "decision": decision,
            "building_id": "13195",
            "timestamp": _now(),
        }

    def _mock_db_for_rejection(self):
        mock_db = _make_db()
        mock_db.work_orders.find_one = AsyncMock(return_value=self._make_wo())
        mock_db.site_settings.find_one = AsyncMock(return_value=None)
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[self._make_approval_doc("reject")])
        mock_db.work_order_approvals.find = MagicMock(return_value=cursor)
        mock_db.work_orders.update_one = AsyncMock()
        return mock_db

    def _mock_db_for_approval(self):
        mock_db = _make_db()
        mock_db.work_orders.find_one = AsyncMock(return_value=self._make_wo())
        mock_db.site_settings.find_one = AsyncMock(return_value={
            "work_order_thresholds": [{
                "max_amount": 5000,
                "approval_mode": "SINGLE_APPROVAL",
                "approval_roles": ["chairman"],
                "approval_required": True,
            }]
        })
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[
            self._make_approval_doc("approve", ec_position="chairman")
        ])
        mock_db.work_order_approvals.find = MagicMock(return_value=cursor)
        mock_db.work_orders.update_one = AsyncMock()
        return mock_db

    async def test_rejection_writes_audit_log(self):
        mock_db = self._mock_db_for_rejection()

        with patch("routers.work_orders.db", mock_db), \
             patch("routers.work_orders.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.work_orders.create_user_notification", new_callable=AsyncMock):
            from routers.work_orders import _check_approval_completion
            await _check_approval_completion("wo-001", "13195")
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        kw = mock_audit.call_args.kwargs
        self.assertEqual(kw.get("action"), "work_order_rejected")
        self.assertEqual(kw.get("resource_type"), "work_order")
        self.assertEqual(kw.get("building_id"), "13195")

    async def test_rejection_notifies_requester(self):
        mock_db = self._mock_db_for_rejection()

        with patch("routers.work_orders.db", mock_db), \
             patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
             patch("routers.work_orders.create_user_notification", new_callable=AsyncMock) as mock_notify:
            from routers.work_orders import _check_approval_completion
            await _check_approval_completion("wo-001", "13195")
            await asyncio.sleep(0)

        mock_notify.assert_called_once()
        kw = mock_notify.call_args.kwargs
        self.assertEqual(kw.get("user_id"), "owner-user-001")
        self.assertIn("Rejected", kw.get("title", ""))

    async def test_approval_writes_audit_log(self):
        mock_db = self._mock_db_for_approval()

        with patch("routers.work_orders.db", mock_db), \
             patch("routers.work_orders.create_audit_log", new_callable=AsyncMock) as mock_audit, \
             patch("routers.work_orders.create_user_notification", new_callable=AsyncMock):
            from routers.work_orders import _check_approval_completion
            await _check_approval_completion("wo-001", "13195")
            await asyncio.sleep(0)

        mock_audit.assert_called_once()
        kw = mock_audit.call_args.kwargs
        self.assertEqual(kw.get("action"), "work_order_approved")
        self.assertEqual(kw.get("resource_type"), "work_order")
        self.assertEqual(kw.get("building_id"), "13195")

    async def test_approval_notifies_requester(self):
        mock_db = self._mock_db_for_approval()

        with patch("routers.work_orders.db", mock_db), \
             patch("routers.work_orders.create_audit_log", new_callable=AsyncMock), \
             patch("routers.work_orders.create_user_notification", new_callable=AsyncMock) as mock_notify:
            from routers.work_orders import _check_approval_completion
            await _check_approval_completion("wo-001", "13195")
            await asyncio.sleep(0)

        mock_notify.assert_called_once()
        kw = mock_notify.call_args.kwargs
        self.assertEqual(kw.get("user_id"), "owner-user-001")
        self.assertIn("Approved", kw.get("title", ""))


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestAuditLogBuildingIsolation(unittest.IsolatedAsyncioTestCase):
    """Every audit_log entry must carry building_id for cross-tenant safety."""

    async def test_meeting_audit_log_carries_correct_building_id(self):
        """building_id in audit_log must match the request's building_id, not a hardcoded default."""
        mock_db = _make_db()
        mock_db.meetings.insert_one = AsyncMock()

        from models.community import MeetingCreate
        meeting_data = MeetingCreate(
            title="EC Meeting", meeting_date="2026-06-01",
            location="Online", agenda=[], attendees=[],
        )

        captured_bid = {}

        async def _capture(*args, **kwargs):
            captured_bid["building_id"] = kwargs.get("building_id")

        with patch("routers.meetings.db", mock_db), \
             patch("routers.meetings.create_audit_log", side_effect=_capture), \
             patch("routers.meetings.broadcast_user_notification", new_callable=AsyncMock), \
             patch("routers.meetings.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_meetings=True)
            from routers.meetings import create_meeting
            await create_meeting(
                meeting=meeting_data,
                current_user=_make_admin(),
                building_id="16244",  # Non-default building
            )
            await asyncio.sleep(0)

        self.assertEqual(captured_bid.get("building_id"), "16244")

    async def test_invoice_pay_audit_log_carries_correct_building_id(self):
        inv = _make_invoice(status="approved")
        mock_db = _make_db()
        mock_db.invoices.find_one = AsyncMock(return_value=inv)
        mock_db.invoices.update_one = AsyncMock()
        mock_db.contractors.update_one = AsyncMock()
        mock_db.expense_transactions.insert_one = AsyncMock()

        captured_bid = {}

        async def _capture(*args, **kwargs):
            captured_bid["building_id"] = kwargs.get("building_id")

        with patch("routers.maintenance.db", mock_db), \
             patch("routers.maintenance.create_audit_log", side_effect=_capture), \
             patch("routers.maintenance.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_finances=True)
            from routers.maintenance import mark_invoice_paid
            await mark_invoice_paid(
                inv_id="inv-001",
                current_user=_make_admin(),
                building_id="demo",
            )
            await asyncio.sleep(0)

        self.assertEqual(captured_bid.get("building_id"), "demo")


if __name__ == "__main__":
    unittest.main()
