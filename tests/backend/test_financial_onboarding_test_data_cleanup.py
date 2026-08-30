"""Tests for DELETE /financials/onboarding/building/{id}/test-data
(backend/routers/financial_onboarding.py::delete_financial_onboarding_test_data).

Gives k6/integration scripts a real, safe cleanup path for is_test_data=True rows created via
the gap-tolerant import + reconstruction-batch flow (GAP-FIN-024) — previously there was no
delete surface for this domain at all. Every delete query in the endpoint unconditionally
includes is_test_data=True as defense in depth; these tests confirm real (non-test) rows are
never touched regardless of the year filter supplied.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.financial_onboarding import router
from utils.auth import get_current_building, get_current_user

BUILDING_ID = "16244"


def _user(role: str = "strata_manager") -> dict:
    return {"id": "user-1", "full_name": "Test User", "role": role, "effective_role": role}


class _FakeCollection:
    def __init__(self, docs=None):
        self._docs: list[dict] = docs or []

    def _matches(self, doc: dict, query: dict) -> bool:
        for key, value in query.items():
            if isinstance(value, dict) and "$in" in value:
                if doc.get(key) not in value["$in"]:
                    return False
            elif isinstance(value, dict) and ("$lte" in value or "$gte" in value):
                doc_val = doc.get(key)
                if "$lte" in value and not (doc_val is not None and doc_val <= value["$lte"]):
                    return False
                if "$gte" in value and not (doc_val is not None and doc_val >= value["$gte"]):
                    return False
            elif doc.get(key) != value:
                return False
        return True

    async def delete_many(self, query: dict):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not self._matches(d, query)]
        deleted = before - len(self._docs)

        class _Result:
            deleted_count = deleted

        return _Result()

    def find(self, query: dict, *_args, **_kwargs):
        matches = [d for d in self._docs if self._matches(d, query)]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def to_list(self, length=None):
                async def _inner():
                    return list(self._items)
                return _inner()

        return _Cursor(matches)


class _FakeDb:
    def __init__(self):
        self.annual_levies = _FakeCollection()
        self.levy_categories = _FakeCollection()
        self.demo_bank_reconstruction_batches = _FakeCollection()
        self.demo_bank_reconstruction_manifests = _FakeCollection()
        self.reconciliation_annual_exceptions = _FakeCollection()


def _build_app(user: dict, building_id: str = BUILDING_ID) -> tuple[TestClient, _FakeDb]:
    fake_db = _FakeDb()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app), fake_db


def _feature_enabled():
    return patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))


def _mocked_mongo(fake_db):
    return patch("routers.financial_onboarding._mongo", return_value=fake_db)


class TestDeleteTestDataSafety:
    def test_only_is_test_data_rows_deleted(self):
        client, fake_db = _build_app(_user())
        fake_db.annual_levies._docs = [
            {"building_id": BUILDING_ID, "year": "2099", "is_test_data": True, "admin_fund": {}},
            {"building_id": BUILDING_ID, "year": "2026", "is_test_data": False, "admin_fund": {}},
        ]
        with _feature_enabled(), _mocked_mongo(fake_db), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data")
        assert resp.status_code == 200
        assert resp.json()["annual_levies_deleted"] == 1
        remaining = fake_db.annual_levies._docs
        assert len(remaining) == 1
        assert remaining[0]["is_test_data"] is False

    def test_year_filter_scopes_deletion(self):
        client, fake_db = _build_app(_user())
        fake_db.annual_levies._docs = [
            {"building_id": BUILDING_ID, "year": "2099", "is_test_data": True},
            {"building_id": BUILDING_ID, "year": "2098", "is_test_data": True},
        ]
        with _feature_enabled(), _mocked_mongo(fake_db), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data?year=2099")
        assert resp.status_code == 200
        assert resp.json()["annual_levies_deleted"] == 1
        remaining_years = {d["year"] for d in fake_db.annual_levies._docs}
        assert remaining_years == {"2098"}

    def test_deletes_batches_and_their_manifests(self):
        client, fake_db = _build_app(_user())
        fake_db.demo_bank_reconstruction_batches._docs = [
            {"building_id": BUILDING_ID, "batch_id": "batch-1", "is_test_data": True,
             "financial_year_start": 2099, "financial_year_end": 2099},
        ]
        fake_db.demo_bank_reconstruction_manifests._docs = [
            {"building_id": BUILDING_ID, "batch_id": "batch-1", "is_test_data": True, "manifest_id": "m-1"},
        ]
        with _feature_enabled(), _mocked_mongo(fake_db), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data?year=2099")
        body = resp.json()
        assert body["reconstruction_batches_deleted"] == 1
        assert body["reconstruction_manifests_deleted"] == 1
        assert fake_db.demo_bank_reconstruction_batches._docs == []
        assert fake_db.demo_bank_reconstruction_manifests._docs == []

    def test_strata_manager_role_permitted(self):
        client, fake_db = _build_app(_user("strata_manager"))
        with _feature_enabled(), _mocked_mongo(fake_db), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            resp = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data")
        assert resp.status_code == 200

    def test_owner_role_forbidden(self):
        client, fake_db = _build_app(_user("owner"))
        with _feature_enabled(), _mocked_mongo(fake_db):
            resp = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data")
        assert resp.status_code == 403

    def test_idempotent_second_call_deletes_nothing(self):
        """Calling cleanup twice (e.g. a k6 teardown retry) must not error and must report 0 the
        second time — the whole point of an idempotent teardown."""
        client, fake_db = _build_app(_user())
        fake_db.annual_levies._docs = [{"building_id": BUILDING_ID, "year": "2099", "is_test_data": True}]
        with _feature_enabled(), _mocked_mongo(fake_db), \
             patch("routers.financial_onboarding._assert_building_access", new=AsyncMock(return_value=BUILDING_ID)), \
             patch("routers.financial_onboarding.create_audit_log", new=AsyncMock()):
            first = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data?year=2099")
            second = client.delete(f"/api/financials/onboarding/building/{BUILDING_ID}/test-data?year=2099")
        assert first.json()["annual_levies_deleted"] == 1
        assert second.status_code == 200
        assert second.json()["annual_levies_deleted"] == 0
