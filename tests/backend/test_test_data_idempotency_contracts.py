"""Regression tests for test-data write/read/cleanup contracts.

These cover API surfaces used by Jest/Playwright/k6 runners that can write to
MongoDB. Test-created records must be tagged with ``is_test_data=True``, hidden
from normal list/search responses, and removable only through explicit test-data
cleanup paths.
"""
from __future__ import annotations

import asyncio as _real_asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request


def _close_coro_task(coro, **_kwargs):
    if _real_asyncio.iscoroutine(coro):
        coro.close()
    return MagicMock()


class _Cursor:
    def __init__(self, docs=None):
        self.docs = docs or []

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, *_args, **_kwargs):
        return self.docs

    def __aiter__(self):
        self._iter = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_document_upload_marks_test_data_and_list_hides_it_by_default():
    from routers import documents

    inserted = {}
    mock_db = SimpleNamespace(
        documents=SimpleNamespace(
            insert_one=AsyncMock(side_effect=lambda doc: inserted.update(doc)),
            find=MagicMock(return_value=_Cursor([])),
            find_one=AsyncMock(return_value=None),
        )
    )
    upload = UploadFile(
        filename="perf-test.pdf",
        file=io.BytesIO(b"%PDF-1.4\n%%EOF"),
        headers=Headers({"content-type": "application/pdf"}),
    )

    with patch("routers.documents.db", mock_db), \
            patch("routers.documents.scan_upload", new=AsyncMock()), \
            patch("routers.documents.get_user_permissions", return_value=SimpleNamespace(
                can_upload_documents=True,
                can_view_documents=True,
            )), \
            patch("routers.documents.create_audit_log", new=AsyncMock()), \
            patch("routers.documents.log_activity", new=AsyncMock()), \
            patch("routers.documents.asyncio") as mock_async:
        mock_async.create_task = _close_coro_task
        await documents.upload_document(
            title="Perf test doc perf-contract",
            description=None,
            category="financial",
            is_public=False,
            allowed_roles="[]",
            is_test_data=True,
            file=upload,
            current_user={"id": "u-1", "full_name": "Test User"},
            building_id="13195",
        )
        await documents.get_documents(
            category=None,
            include_test_data=False,
            current_user={"id": "u-1", "role": "super_admin", "is_approved": True},
            building_id="13195",
        )
        with pytest.raises(Exception):
            await documents.get_document(
                doc_id="perf-test-doc",
                include_test_data=False,
                current_user={"id": "u-1", "role": "super_admin", "is_approved": True},
                building_id="13195",
            )

    assert inserted["is_test_data"] is True
    query = mock_db.documents.find.call_args.args[0]
    assert query["is_test_data"] == {"$ne": True}
    detail_query = mock_db.documents.find_one.await_args.args[0]
    assert detail_query["is_test_data"] == {"$ne": True}


@pytest.mark.asyncio
async def test_decisions_tag_filter_and_delete_only_test_records():
    from routers import decisions

    inserted = {}
    decision_id = ObjectId("64f000000000000000000001")
    mock_db = SimpleNamespace(
        decisions_counter=SimpleNamespace(
            find_one_and_update=AsyncMock(return_value={"seq": 7})
        ),
        decisions=SimpleNamespace(
            insert_one=AsyncMock(side_effect=lambda doc: inserted.update(doc) or SimpleNamespace(inserted_id=decision_id)),
            count_documents=AsyncMock(return_value=0),
            find=MagicMock(return_value=_Cursor([])),
            find_one=AsyncMock(side_effect=[None, {"is_test_data": False}, {"is_test_data": True}]),
            delete_one=AsyncMock(return_value=SimpleNamespace(deleted_count=1)),
        ),
    )
    payload = decisions.DecisionCreate(
        building_id="spoofed",
        meeting_type="ec_meeting",
        meeting_date="2026-04-15",
        motion_title="Perf decision register perf-contract",
        motion_text="Performance test decision body.",
        resolution_type="ordinary",
        result="passed",
        is_test_data=True,
    )
    user = {"id": "u-1", "role": "ec_member", "full_name": "EC User"}

    with patch("routers.decisions._get_db", return_value=mock_db), \
            patch("routers.decisions.create_audit_log", new=AsyncMock()), \
            patch("routers.decisions.asyncio") as mock_async:
        mock_async.create_task = _close_coro_task
        await decisions.create_decision(payload, current_user=user, building_id="13195")
        await decisions.list_decisions(
            current_user=user,
            building_id="13195",
            meeting_type=None,
            resolution_type=None,
            result=None,
            date_from=None,
            date_to=None,
            tags=None,
            page=1,
            page_size=20,
            include_test_data=False,
        )
        with pytest.raises(Exception):
            await decisions.get_decision(
                str(decision_id),
                include_test_data=False,
                building_id="13195",
                current_user=user,
            )
        with pytest.raises(Exception):
            await decisions.delete_decision(str(decision_id), current_user=user, building_id="13195")
        await decisions.delete_decision(str(decision_id), current_user=user, building_id="13195")

    assert inserted["building_id"] == "13195"
    assert inserted["is_test_data"] is True
    assert mock_db.decisions.count_documents.call_args.args[0]["is_test_data"] == {"$ne": True}
    assert mock_db.decisions.find_one.await_args_list[0].args[0]["is_test_data"] == {"$ne": True}
    assert mock_db.decisions.delete_one.await_args.args[0]["is_test_data"] is True


@pytest.mark.asyncio
async def test_trust_batches_tag_filter_and_delete_only_test_records():
    from models.trust_accounting import BatchType, FundType, PaymentBatchCreate, PaymentItem
    from routers import trust_accounting

    inserted = {}
    batch_id = ObjectId("64f000000000000000000002")
    mock_db = SimpleNamespace(
        trust_ledger_batches=SimpleNamespace(
            insert_one=AsyncMock(side_effect=lambda doc: inserted.update(doc) or SimpleNamespace(inserted_id=batch_id)),
            count_documents=AsyncMock(return_value=0),
            find=MagicMock(return_value=_Cursor([])),
            find_one=AsyncMock(side_effect=[{"is_test_data": False}, {"is_test_data": True}]),
            delete_one=AsyncMock(return_value=SimpleNamespace(deleted_count=1)),
        )
    )
    payload = PaymentBatchCreate(
        building_id="spoofed",
        fund_type=FundType.ADMIN,
        batch_type=BatchType.DIRECT,
        description="Perf dual-approve batch contract",
        payment_date="2026-04-30",
        is_test_data=True,
        items=[
            PaymentItem(
                payee_name="Test Vendor",
                bsb="082-001",
                account_number="123456789",
                amount=1200.00,
                reference="PERF-001",
            )
        ],
    )
    user = {"id": "u-1", "role": "ec_member", "full_name": "EC User"}

    with patch("routers.trust_accounting._get_db", return_value=mock_db), \
            patch("utils.encryption.encrypt_field", side_effect=lambda value: f"enc:{value}"), \
            patch("routers.trust_accounting.create_audit_log", new=AsyncMock()), \
            patch(
                "routers.trust_accounting.are_cutover_features_enabled",
                new_callable=AsyncMock, return_value=False,
            ), \
            patch("routers.trust_accounting.asyncio") as mock_async:
        # are_cutover_features_enabled mocked False so _block_v1_write_if_pg_primary's
        # trust_pg_ledger_enabled 409 (live for building 13195 since 2026-08-04) doesn't mask
        # this test's actual target: the V1 batch-create/list is_test_data tag-filter contract.
        mock_async.create_task = _close_coro_task
        await trust_accounting.create_payment_batch(payload, building_id="13195", current_user=user)
        await trust_accounting.list_payment_batches(
            building_id="13195",
            batch_status=None,
            include_test_data=False,
            page=1,
            page_size=20,
            current_user=user,
        )
        with pytest.raises(Exception):
            await trust_accounting.delete_payment_batch(str(batch_id), building_id="13195", current_user=user)
        await trust_accounting.delete_payment_batch(str(batch_id), building_id="13195", current_user=user)

    assert inserted["building_id"] == "13195"
    assert inserted["is_test_data"] is True
    assert mock_db.trust_ledger_batches.count_documents.call_args.args[0]["is_test_data"] == {"$ne": True}
    assert mock_db.trust_ledger_batches.delete_one.await_args.args[0]["is_test_data"] is True


@pytest.mark.asyncio
async def test_login_audits_can_be_marked_test_data_and_security_reads_hide_them():
    from routers import auth, security

    inserted = {}
    audit_collection = SimpleNamespace(
        insert_one=AsyncMock(side_effect=lambda doc: inserted.update(doc)),
        aggregate=MagicMock(return_value=_Cursor([{}])),
        delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=4)),
    )
    mock_db = SimpleNamespace(login_audit_logs=audit_collection)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [
            (b"x-test-data", b"true"),
            (b"user-agent", b"k6-test"),
        ],
        "client": ("127.0.0.1", 12345),
    })

    with patch("routers.auth.db", mock_db), \
            patch("routers.security.db", mock_db):
        await auth._log_login_attempt(
            user=None,
            email="attacker@example.test",
            request=request,
            status_str="failed",
            failure_reason="invalid_credentials",
        )
        await security.get_security_stats(current_user={"role": "super_admin"})
        result = await security.cleanup_test_login_audits(current_user={"role": "super_admin"})

    assert inserted["is_test_data"] is True
    pipeline = audit_collection.aggregate.call_args.args[0]
    assert pipeline[0]["$match"]["is_test_data"] == {"$ne": True}
    audit_collection.delete_many.assert_awaited_once_with({"is_test_data": True})
    assert result["deleted"] == 4


@pytest.mark.asyncio
async def test_financial_onboarding_cleanup_deletes_only_tagged_test_year_rows():
    from routers import financial_onboarding

    batches = SimpleNamespace(
        find=MagicMock(return_value=_Cursor([{"batch_id": "batch-1"}])),
        delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=1)),
    )
    manifests = SimpleNamespace(delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=1)))
    annual_levies = SimpleNamespace(delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=2)))
    levy_categories = SimpleNamespace(delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=3)))
    reconciliation_exceptions = SimpleNamespace(delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=0)))
    mongo_db = SimpleNamespace(
        demo_bank_reconstruction_batches=batches,
        demo_bank_reconstruction_manifests=manifests,
        annual_levies=annual_levies,
        levy_categories=levy_categories,
        reconciliation_annual_exceptions=reconciliation_exceptions,
    )

    with patch("routers.financial_onboarding._mongo", return_value=mongo_db), \
            patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value="13195")), \
            patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
        result = await financial_onboarding.delete_financial_onboarding_test_data(
            "13195",
            year=2099,
            current_user={"id": "u-1", "role": "strata_manager", "name": "Manager"},
            current_building="13195",
        )

    batch_query = batches.find.call_args.args[0]
    assert batch_query["building_id"] == "13195"
    assert batch_query["is_test_data"] is True
    assert batch_query["financial_year_start"] == {"$lte": 2099}
    assert batch_query["financial_year_end"] == {"$gte": 2099}
    assert manifests.delete_many.await_args.args[0]["batch_id"] == {"$in": ["batch-1"]}
    assert annual_levies.delete_many.await_args.args[0]["is_test_data"] is True
    assert annual_levies.delete_many.await_args.args[0]["year"] == {"$in": ["2099", 2099]}
    assert levy_categories.delete_many.await_args.args[0]["year"] == {"$in": ["2099", 2099]}
    assert result == {
        "annual_levies_deleted": 2,
        "levy_categories_deleted": 3,
        "reconstruction_batches_deleted": 1,
        "reconstruction_manifests_deleted": 1,
        "reconciliation_exceptions_deleted": 0,
    }
