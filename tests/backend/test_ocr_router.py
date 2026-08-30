"""Tests for backend/routers/ocr.py + backend/services/ocr/unlimited_ocr_client.py (GAP-OCR-001).

Uses the real FastAPI app + TestClient + dependency_overrides pattern (matching
test_financial_onboarding_dispatch_and_toggles.py from the same session) — TestClient runs
BackgroundTasks synchronously before returning, so create_ocr_job's async dispatch is fully
exercised end-to-end within a single request/response cycle here, without needing a real event
loop juggling act. `routers.ocr.db` and `routers.ocr.get_provider_registry` are patched with an
in-memory fake (read-your-writes semantics) — no real Mongo connection.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from integrations.envelopes import OCRExtractionResult, OCRFieldResult
from routers.ocr import router
from services.ocr.unlimited_ocr_client import (
    UNLIMITED_OCR,
    UnlimitedOCRConfig,
    normalize_unlimited_ocr_payload,
)
from utils.auth import get_current_building, get_current_user

BUILDING_A = "16244"  # Sierra demo — never "13195" in tests
BUILDING_B = "77001"  # a second, distinct building for isolation tests


def _user(role: str = "strata_manager") -> dict:
    return {"id": "user-1", "name": "Test User", "role": role, "effective_role": role}


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc: dict):
        self._docs.append(dict(doc))

    async def find_one(self, query: dict):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(self, query: dict, update: dict):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(update.get("$set", {}))
                return

    def find(self, query: dict):
        matches = [doc for doc in self._docs if all(doc.get(k) == v for k, v in query.items())]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *args, **kwargs):
                return self

            def to_list(self, n):
                async def _inner():
                    return list(self._items[:n])
                return _inner()

        return _Cursor(matches)


class _FakeDb:
    def __init__(self):
        self.ocr_jobs = _FakeCollection()
        self.ocr_results = _FakeCollection()
        self.documents = _FakeCollection()


class _FakeOCRProvider:
    """Stand-in for a registered OCRProvider — extract_invoice is fully controllable per test."""

    def __init__(self, extraction=None, error: Exception | None = None):
        self.extraction = extraction
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    async def extract_invoice(self, document_bytes: bytes, mime_type: str):
        self.calls.append((document_bytes, mime_type))
        if self.error:
            raise self.error
        return self.extraction


def _extraction(confidence: float = 0.95, total_cents: int = 11000) -> OCRExtractionResult:
    field = OCRFieldResult(value="Acme Pty Ltd", confidence=confidence)
    return OCRExtractionResult(
        vendor_name=field, vendor_abn=field, invoice_number=field, issue_date=field, due_date=field,
        total_cents=total_cents, gst_cents=1000, subtotal_cents=10000, line_items=(),
        raw_text="INVOICE Acme Pty Ltd $110.00", overall_confidence=confidence, is_tax_invoice=True,
        provider="mock_ocr",
    )


def _build_app(user: dict, building_id: str = BUILDING_A) -> tuple[TestClient, _FakeDb]:
    fake_db = _FakeDb()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_building] = lambda: building_id
    return TestClient(app), fake_db


def _feature_enabled():
    return patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))


class TestRoleGate:
    def test_guest_role_cannot_create_job(self):
        client, fake_db = _build_app(_user("guest"))
        with _feature_enabled(), patch("routers.ocr.db", fake_db):
            resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
        assert resp.status_code == 403

    def test_strata_manager_can_create_job(self):
        client, fake_db = _build_app(_user("strata_manager"))
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
        assert resp.status_code == 201


class TestIsTestDataPropagation:
    def test_is_test_data_stamped_on_job_and_result(self):
        client, fake_db = _build_app(_user())
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto", "is_test_data": "true"},
            )
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job_doc = fake_db.ocr_jobs._docs[0]
        assert job_doc["id"] == job_id
        assert job_doc["is_test_data"] is True
        # TestClient runs BackgroundTasks synchronously — the extraction has already completed.
        assert job_doc["status"] in ("completed", "needs_review")
        result_doc = fake_db.ocr_results._docs[0]
        assert result_doc["is_test_data"] is True

    def test_is_test_data_defaults_false(self):
        client, fake_db = _build_app(_user())
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
        assert fake_db.ocr_jobs._docs[0]["is_test_data"] is False

    def test_publish_document_propagates_job_is_test_data(self):
        client, fake_db = _build_app(_user())
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            create_resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto", "is_test_data": "true"},
            )
            job_id = create_resp.json()["id"]
            publish_resp = client.post(f"/api/ocr/jobs/{job_id}/publish-document", data={})
        assert publish_resp.status_code == 200
        doc = fake_db.documents._docs[0]
        assert doc["is_test_data"] is True


class TestAsyncDispatch:
    def test_extraction_dispatched_via_background_task_not_inline(self):
        """Regression guard for the fix: create_ocr_job must schedule extraction via
        BackgroundTasks.add_task, never await the provider call directly in the handler."""
        client, fake_db = _build_app(_user())
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()), \
             patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
            resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
        assert resp.status_code == 201
        mock_add_task.assert_called_once()
        # Provider extraction never ran inline — add_task was patched to a no-op.
        assert provider.calls == []
        # The job was returned in "processing", not a terminal status, since the (mocked-away)
        # background task never actually ran.
        assert resp.json()["status"] == "processing"

    def test_provider_failure_marks_job_failed_not_500(self):
        client, fake_db = _build_app(_user())
        provider = _FakeOCRProvider(error=RuntimeError("provider unavailable"))
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
        assert resp.status_code == 201  # the HTTP call itself always succeeds
        job_doc = fake_db.ocr_jobs._docs[0]
        assert job_doc["status"] == "failed"
        assert job_doc["error_code"] == "ocr_provider_failed"
        assert "provider unavailable" in job_doc["error_message"]

    def test_terminal_status_write_failure_does_not_crash_request(self):
        """If the terminal-status update_one call itself raises (transient DB error) after a
        successful extraction, _run_ocr_job must not propagate — BackgroundTasks runs
        synchronously under TestClient, so an uncaught exception here would surface as a 500
        on this request. The job is left un-updated (still "processing") rather than crashing;
        this is the documented, narrower guarantee the fix provides — see _run_ocr_job's
        docstring: it cannot GUARANTEE a terminal status is persisted if the write layer itself
        is down, only that the failure doesn't crash the caller."""
        client, fake_db = _build_app(_user())
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)

        async def _boom(*args, **kwargs):
            raise RuntimeError("Mongo write timeout")
        fake_db.ocr_jobs.update_one = _boom

        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()), \
             patch("routers.ocr.logger") as mock_logger:
            resp = client.post(
                "/api/ocr/jobs",
                files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
        assert resp.status_code == 201
        mock_logger.critical.assert_called_once()
        job_doc = fake_db.ocr_jobs._docs[0]
        assert job_doc["status"] == "processing"  # the update never landed — left as created


class TestBuildingIsolation:
    def test_job_list_scoped_to_building(self):
        client_a, fake_db = _build_app(_user(), building_id=BUILDING_A)
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            client_a.post(
                "/api/ocr/jobs",
                files={"file": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )

            app_b = FastAPI()
            app_b.include_router(router, prefix="/api")
            app_b.dependency_overrides[get_current_user] = lambda: _user()
            app_b.dependency_overrides[get_current_building] = lambda: BUILDING_B
            client_b = TestClient(app_b)
            resp_b = client_b.get("/api/ocr/jobs")

        assert resp_b.status_code == 200
        assert resp_b.json() == []  # building B sees nothing building A created

    def test_get_job_cross_building_returns_404(self):
        client_a, fake_db = _build_app(_user(), building_id=BUILDING_A)
        provider = _FakeOCRProvider(extraction=_extraction())
        registry = AsyncMock()
        registry.get_ocr = AsyncMock(return_value=provider)
        with _feature_enabled(), patch("routers.ocr.db", fake_db), \
             patch("routers.ocr.get_provider_registry", return_value=registry), \
             patch("routers.ocr.scan_upload", new=AsyncMock()):
            create_resp = client_a.post(
                "/api/ocr/jobs",
                files={"file": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"source_type": "invoice", "provider": "auto"},
            )
            job_id = create_resp.json()["id"]

            app_b = FastAPI()
            app_b.include_router(router, prefix="/api")
            app_b.dependency_overrides[get_current_user] = lambda: _user()
            app_b.dependency_overrides[get_current_building] = lambda: BUILDING_B
            client_b = TestClient(app_b)
            resp_b = client_b.get(f"/api/ocr/jobs/{job_id}")

        assert resp_b.status_code == 404


class TestNormalizeUnlimitedOcrPayload:
    def test_maps_dollar_amounts_to_cents(self):
        result = normalize_unlimited_ocr_payload({
            "vendor_name": "Acme Pty Ltd", "total": "110.00", "gst": "10.00", "subtotal": "100.00",
            "overall_confidence": 0.9,
        })
        assert result.total_cents == 11000
        assert result.gst_cents == 1000
        assert result.subtotal_cents == 10000
        assert result.vendor_name.value == "Acme Pty Ltd"
        assert result.provider == UNLIMITED_OCR

    def test_confidence_clamped_to_0_1(self):
        result = normalize_unlimited_ocr_payload({"overall_confidence": 5.0})
        assert result.overall_confidence == 1.0
        result2 = normalize_unlimited_ocr_payload({"overall_confidence": -3.0})
        assert result2.overall_confidence == 0.0

    def test_line_items_converted(self):
        result = normalize_unlimited_ocr_payload({
            "line_items": [{"description": "Widget", "quantity": 2, "unit_price": "5.00", "total": "10.00"}],
        })
        assert len(result.line_items) == 1
        assert result.line_items[0].description == "Widget"
        assert result.line_items[0].unit_price_cents == 500
        assert result.line_items[0].total_cents == 1000

    def test_missing_fields_produce_none_not_crash(self):
        result = normalize_unlimited_ocr_payload({})
        assert result.total_cents is None
        assert result.vendor_name.value is None
        assert result.line_items == ()


class TestUnlimitedOCRConfigAvailability:
    def test_disabled_by_default(self):
        config = UnlimitedOCRConfig(
            enabled=False, backend="disabled", endpoint="", api_key_present=False,
            max_pages=25, max_bytes=1000, timeout_seconds=60, pdf_dpi=300, default_mode="auto",
            model_version="x",
        )
        assert config.is_available is False
        assert "UNLIMITED_OCR_ENABLED" in config.unavailable_reason

    def test_private_endpoint_requires_endpoint_url(self):
        config = UnlimitedOCRConfig(
            enabled=True, backend="private_endpoint", endpoint="", api_key_present=False,
            max_pages=25, max_bytes=1000, timeout_seconds=60, pdf_dpi=300, default_mode="auto",
            model_version="x",
        )
        assert config.is_available is False
        config_ok = UnlimitedOCRConfig(
            enabled=True, backend="private_endpoint", endpoint="http://localhost:8010",
            api_key_present=False, max_pages=25, max_bytes=1000, timeout_seconds=60, pdf_dpi=300,
            default_mode="auto", model_version="x",
        )
        assert config_ok.is_available is True

    def test_baidu_cloud_requires_api_key(self):
        config = UnlimitedOCRConfig(
            enabled=True, backend="baidu_cloud", endpoint="", api_key_present=False,
            max_pages=25, max_bytes=1000, timeout_seconds=60, pdf_dpi=300, default_mode="auto",
            model_version="x",
        )
        assert config.is_available is False
        assert "UNLIMITED_OCR_API_KEY" in config.unavailable_reason


class TestProviderRegistryFallback:
    """registry.get_ocr()'s fallback-to-mock_ocr behaviour, added alongside the unlimited_ocr
    registration — previously untested."""

    @pytest.mark.asyncio
    async def test_unavailable_requested_provider_falls_back_to_mock(self):
        from integrations.registry import MOCK_OCR, ProviderRegistry

        registry = ProviderRegistry()
        mock_provider = AsyncMock()
        mock_provider.name = MOCK_OCR
        mock_provider.is_available = True
        unavailable_provider = AsyncMock()
        unavailable_provider.name = UNLIMITED_OCR
        unavailable_provider.is_available = False
        unavailable_provider.unavailable_reason = "not configured"
        registry.register_ocr(mock_provider)
        registry.register_ocr(unavailable_provider)
        registry._get_preference = AsyncMock(return_value={"ocr": UNLIMITED_OCR})

        resolved = await registry.get_ocr("16244")
        assert resolved is mock_provider
