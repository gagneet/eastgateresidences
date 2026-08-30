# @featuretrace:documents — Postgres documents repository contract.
# Layer: service
# Data flow: documents_repo._require_tenant / _row_to_document -> documents.documents (building-scoped).
# Related: backend/db_postgres/repos/documents_repo.py
#          backend/services/documents_store.py
"""Contract tests that need no database.

The live insert/read/archive path was verified against production Postgres on
2026-08-29 (insert -> read back -> count -> soft-archive -> idempotent re-archive).
What is asserted here is the part that must hold even when nobody is watching: that a
missing tenant context RAISES instead of quietly returning zero rows.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_documents_repo.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from db_postgres.repos import documents_repo  # noqa: E402
from db_postgres.repos.documents_repo import DocumentVisibility  # noqa: E402


class TestTenantGuard:
    """documents.documents RLS has no bypass clause — a missing tenant means 0 rows, silently."""

    @pytest.mark.asyncio
    async def test_unknown_building_raises_rather_than_returning_empty(self):
        with patch.object(documents_repo, "resolve_scheme_context", new=AsyncMock(return_value=None)):
            with pytest.raises(documents_repo.TenantContextMissing):
                await documents_repo._require_tenant("nope")

    @pytest.mark.asyncio
    async def test_partial_scheme_context_also_raises(self):
        """A scheme with no tenant_id is just as unusable as no scheme at all."""
        with patch.object(
            documents_repo, "resolve_scheme_context",
            new=AsyncMock(return_value={"scheme_id": "s", "tenant_id": None}),
        ):
            with pytest.raises(documents_repo.TenantContextMissing):
                await documents_repo._require_tenant("13195")

    @pytest.mark.asyncio
    async def test_the_error_names_the_reason_not_just_the_failure(self):
        with patch.object(documents_repo, "resolve_scheme_context", new=AsyncMock(return_value=None)):
            with pytest.raises(documents_repo.TenantContextMissing) as exc:
                await documents_repo._require_tenant("nope")
        assert "RLS would return zero rows and no error" in str(exc.value)

    @pytest.mark.asyncio
    async def test_valid_context_is_returned_as_strings(self):
        with patch.object(
            documents_repo, "resolve_scheme_context",
            new=AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"}),
        ):
            ctx = await documents_repo._require_tenant("13195")
        assert ctx == {"tenant_id": "t-1", "scheme_id": "s-1"}


class TestRowShape:
    def _row(self, **over):
        base = dict(
            document_id="d-1", title="T", original_filename="f.pdf",
            mime_type="application/pdf", storage_key="k", file_size_bytes=10,
            folder_id=None, allowed_roles=["owner"], tags=["t"],
            retention_class="seven_year", is_archived=False, uploader_user_id=None,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        base.update(over)
        return SimpleNamespace(**base)

    def test_source_store_is_stamped_so_a_caller_can_tell_where_it_came_from(self):
        assert documents_repo._row_to_document(self._row())["source_store"] == "postgres"

    def test_null_uuid_columns_never_become_the_string_none(self):
        """Each null maps to what its RESPONSE FIELD accepts, not to one blanket value.

        `folder_id` is `Optional[str]` so None is correct; `uploaded_by` is a plain
        `str` with a `""` default, so None there fails validation. The two differ on
        purpose — mapping both to None passes this repo's own tests and then 500s at
        the response model.
        """
        doc = documents_repo._row_to_document(self._row())
        assert doc["folder_id"] is None
        assert doc["uploaded_by"] == ""
        assert "None" not in (doc["folder_id"] or "")

    def test_uuid_columns_are_stringified(self):
        doc = documents_repo._row_to_document(self._row(folder_id="f-1", uploader_user_id="u-1"))
        assert doc["folder_id"] == "f-1"
        assert doc["uploaded_by"] == "u-1"

    def test_missing_file_size_reads_as_zero_not_none(self):
        assert documents_repo._row_to_document(self._row(file_size_bytes=None))["file_size"] == 0

    def test_category_is_carried_in_the_first_tag(self):
        doc = documents_repo._row_to_document(self._row(tags=["insurance", "2026"]))
        assert doc["category"] == "insurance"

    def test_a_document_with_no_tags_still_has_a_category(self):
        """A missing category is a display detail, not a reason to refuse the row."""
        assert documents_repo._row_to_document(self._row(tags=[]))["category"] == "general"

    def test_timestamps_are_serialised_not_left_as_datetime(self):
        doc = documents_repo._row_to_document(self._row())
        assert isinstance(doc["created_at"], str)
        assert isinstance(doc["updated_at"], str)


class TestResponseModelCompatibility:
    """The shape must satisfy server.py's DocumentResponse.

    Without this, promoting the documents domain would 500 on the FIRST Postgres-served
    read — `category` is a required field with no default and no column behind it, and
    the failure could only ever appear in production, after a routing change, on a path
    no test exercises while the domain is still Mongo-primary.
    """

    def _row(self, **over):
        base = dict(
            document_id="d-1", title="Insurance certificate", original_filename="cert.pdf",
            mime_type="application/pdf", storage_key="k", file_size_bytes=2048,
            folder_id=None, allowed_roles=["owner"], tags=["insurance"],
            retention_class="seven_year", is_archived=False, uploader_user_id=None,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        base.update(over)
        return SimpleNamespace(**base)

    def test_a_postgres_row_validates_against_the_live_response_model(self):
        from server import DocumentResponse

        doc = documents_repo._row_to_document(self._row())
        doc["building_id"] = "13195"
        response = DocumentResponse(**{k: v for k, v in doc.items() if k != "source_store"})
        assert response.id == "d-1"
        assert response.category == "insurance"
        assert response.file_name == "cert.pdf"
        assert response.file_type == "application/pdf"
        assert response.file_size == 2048

    def test_a_tagless_postgres_row_also_validates(self):
        from server import DocumentResponse

        doc = documents_repo._row_to_document(self._row(tags=[]))
        doc["building_id"] = "13195"
        assert DocumentResponse(**{k: v for k, v in doc.items() if k != "source_store"}).category == "general"


class TestVisibilityIsRequired:
    """The defect this class exists to prevent, found by audit 2026-08-29.

    The first version took `allowed_roles: list[str] | None = None` and treated None as
    "no filter". server.py never passed it. On a promoted domain that returns EVERY
    document in the building to ANY caller — and GET /documents uses get_optional_user,
    so that includes callers with no session at all. It was inert only because the
    domain is not promoted, which is exactly how this shape of defect reaches
    production: first observable immediately after a routing change, on a path no test
    covers while the domain is still Mongo-primary.
    """

    def test_list_documents_has_no_default_visibility(self):
        """Omitting the context must be a TypeError, not an unfiltered read."""
        import inspect

        sig = inspect.signature(documents_repo.list_documents)
        param = sig.parameters["visibility"]
        assert param.default is inspect.Parameter.empty, (
            "visibility must have no default — a default is how the unfiltered read "
            "got shipped in the first place"
        )
        assert param.kind is inspect.Parameter.KEYWORD_ONLY

    def test_reading_everything_requires_saying_so(self):
        v = DocumentVisibility.unrestricted_read()
        assert v.unrestricted is True
        assert DocumentVisibility.for_roles(["owner"]).unrestricted is False

    @pytest.mark.asyncio
    async def test_a_restricted_caller_with_no_expressible_branch_sees_nothing(self):
        """Not "everything" — nothing. An empty branch list must never mean no WHERE."""
        with patch.object(
            documents_repo, "resolve_scheme_context",
            new=AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"}),
        ), patch.object(documents_repo, "_has_is_public_column", new=AsyncMock(return_value=True)):
            rows = await documents_repo.list_documents(
                "13195",
                visibility=DocumentVisibility(
                    viewer_roles=(), viewer_user_id=None, include_public=False,
                ),
            )
        assert rows == []

    @pytest.mark.asyncio
    async def test_missing_is_public_column_refuses_rather_than_approximating(self):
        """Both approximations are wrong and neither announces itself."""
        with patch.object(
            documents_repo, "resolve_scheme_context",
            new=AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"}),
        ), patch.object(documents_repo, "_has_is_public_column", new=AsyncMock(return_value=False)):
            with pytest.raises(documents_repo.VisibilityNotExpressible) as exc:
                await documents_repo.list_documents(
                    "13195", visibility=DocumentVisibility.for_roles(["owner"]),
                )
        assert "is_public" in str(exc.value)

    @pytest.mark.asyncio
    async def test_a_legacy_non_uuid_folder_id_refuses_rather_than_cast_erroring(self):
        with patch.object(
            documents_repo, "resolve_scheme_context",
            new=AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"}),
        ), patch.object(documents_repo, "_has_is_public_column", new=AsyncMock(return_value=True)):
            with pytest.raises(documents_repo.VisibilityNotExpressible) as exc:
                await documents_repo.list_documents(
                    "13195",
                    visibility=DocumentVisibility.for_roles(["owner"]),
                    folder_id="legacy-folder-123",
                )
        assert "not a UUID" in str(exc.value)

    @pytest.mark.asyncio
    async def test_a_non_uuid_viewer_id_contributes_no_branch_rather_than_erroring(self):
        """Mongo and Postgres ids for one person differ (footgun #24)."""
        with patch.object(
            documents_repo, "resolve_scheme_context",
            new=AsyncMock(return_value={"scheme_id": "s-1", "tenant_id": "t-1"}),
        ), patch.object(documents_repo, "_has_is_public_column", new=AsyncMock(return_value=True)):
            rows = await documents_repo.list_documents(
                "13195",
                visibility=DocumentVisibility(
                    viewer_roles=(), viewer_user_id="mongo-style-id", include_public=False,
                ),
            )
        assert rows == []
