# @featuretrace:documents — store-agnostic documents dispatch and dual-write.
# Layer: test
# Data flow: documents_store.read_documents / write_document -> store_router + both stores (building-scoped).
# Related: backend/services/documents_store.py
#          backend/db_postgres/repos/documents_repo.py
"""Contract tests for the documents seam.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_documents_store.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from services import documents_store  # noqa: E402
from db_postgres.repos.documents_repo import DocumentVisibility  # noqa: E402
from services.store_router import StoreDecision  # noqa: E402

# Every read test states who is asking. There is no default, deliberately.
VIS = DocumentVisibility.for_roles(["owner"], viewer_user_id=None)


def _decision(source: str, operation: str = "read") -> StoreDecision:
    return StoreDecision(
        domain="documents", building_id="13195", operation=operation,
        source=source, shadow_enabled=False, blocked_reason=None,
    )


class _NoMirror(StoreDecision):
    """A Postgres-primary domain that has been decommissioned off MongoDB (Phase 5)."""

    @property
    def mirror_to_mongo(self) -> bool:
        return False


def _mongo_db(docs: list[dict] | None = None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(docs or []))
    db.documents.find = MagicMock(return_value=cursor)
    db.documents.update_one = AsyncMock()
    db.documents.count_documents = AsyncMock(return_value=len(docs or []))
    return db


class TestReadDocuments:
    @pytest.mark.asyncio
    async def test_mongo_primary_never_touches_postgres(self):
        db = _mongo_db([{"id": "m1", "title": "From Mongo"}])
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=_decision("mongo"))):
            result = await documents_store.read_documents("13195", visibility=VIS)
        assert result["source"] == "mongo"
        assert [d["id"] for d in result["documents"]] == ["m1"]

    @pytest.mark.asyncio
    async def test_postgres_result_is_served_when_present(self):
        db = _mongo_db([{"id": "m1"}])
        pg_docs = [{"id": "p1", "title": "From PG", "source_store": "postgres"}]
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=_decision("postgres"))), \
             patch("db_postgres.repos.documents_repo.list_documents", new=AsyncMock(return_value=pg_docs)):
            result = await documents_store.read_documents("13195", visibility=VIS)
        assert result["source"] == "postgres"
        assert [d["id"] for d in result["documents"]] == ["p1"]

    @pytest.mark.asyncio
    async def test_empty_postgres_falls_back_to_mongo_during_coexistence(self):
        """documents.documents is empty for every building; a strict read would blank the page."""
        db = _mongo_db([{"id": "m1"}, {"id": "m2"}])
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=_decision("postgres"))), \
             patch("db_postgres.repos.documents_repo.list_documents", new=AsyncMock(return_value=[])):
            result = await documents_store.read_documents("13195", visibility=VIS)
        assert result["source"] == "mongo_fallback_pg_empty"
        assert len(result["documents"]) == 2

    @pytest.mark.asyncio
    async def test_postgres_failure_is_distinguished_from_postgres_empty(self):
        """'PG is empty' and 'PG broke' must never collapse into one value."""
        db = _mongo_db([{"id": "m1"}])
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=_decision("postgres"))), \
             patch("db_postgres.repos.documents_repo.list_documents",
                   new=AsyncMock(side_effect=RuntimeError("rls"))):
            result = await documents_store.read_documents("13195", visibility=VIS)
        assert result["source"] == "mongo_fallback_pg_unavailable"


class TestWriteDocument:
    COMMON = dict(
        title="T", original_filename="f.pdf", mime_type="application/pdf",
        storage_key="k", file_size_bytes=1,
    )

    @pytest.mark.asyncio
    async def test_postgres_write_is_mirrored_into_mongo(self):
        db = _mongo_db()
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store",
                          new=AsyncMock(return_value=_decision("postgres", "write"))), \
             patch("db_postgres.repos.documents_repo.create_document",
                   new=AsyncMock(return_value={"id": "p1", "title": "T"})):
            result = await documents_store.write_document("13195", **self.COMMON)
        assert result["postgres_written"] is True
        assert result["mongo_written"] is True
        assert result["mirror_error"] is None
        db.documents.update_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_postgres_failure_falls_back_to_mongo_and_reports_it(self):
        db = _mongo_db()
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store",
                          new=AsyncMock(return_value=_decision("postgres", "write"))), \
             patch("db_postgres.repos.documents_repo.create_document",
                   new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await documents_store.write_document("13195", **self.COMMON)
        assert result["postgres_written"] is False
        assert result["mongo_written"] is True
        assert "postgres_write_failed" in result["mirror_error"]

    @pytest.mark.asyncio
    async def test_mirror_failure_does_not_fail_a_committed_postgres_write(self):
        """The authoritative write already committed — but the hole must be reported."""
        db = _mongo_db()
        db.documents.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store",
                          new=AsyncMock(return_value=_decision("postgres", "write"))), \
             patch("db_postgres.repos.documents_repo.create_document",
                   new=AsyncMock(return_value={"id": "p1"})):
            result = await documents_store.write_document("13195", **self.COMMON)
        assert result["postgres_written"] is True
        assert result["mongo_written"] is False
        assert "mongo_mirror_failed" in result["mirror_error"]

    @pytest.mark.asyncio
    async def test_mongo_primary_write_failure_still_raises(self):
        """With no Postgres write to fall back on, a Mongo failure is a real failure."""
        db = _mongo_db()
        db.documents.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store",
                          new=AsyncMock(return_value=_decision("mongo", "write"))):
            with pytest.raises(RuntimeError):
                await documents_store.write_document("13195", **self.COMMON)


class TestVisibilityRefusal:
    """A refusal is not an error and not an empty result — it is its own state."""

    @pytest.mark.asyncio
    async def test_a_refusal_serves_mongo_and_is_reported_distinctly(self):
        from db_postgres.repos.documents_repo import VisibilityNotExpressible

        db = _mongo_db([{"id": "m1"}, {"id": "m2"}])
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=_decision("postgres"))), \
             patch("db_postgres.repos.documents_repo.list_documents",
                   new=AsyncMock(side_effect=VisibilityNotExpressible("no is_public column"))):
            result = await documents_store.read_documents("13195", visibility=VIS)
        assert result["source"] == "mongo_fallback_pg_cannot_express"
        assert result["pg_refused_reason"] == "no is_public column"
        assert len(result["documents"]) == 2

    @pytest.mark.asyncio
    async def test_refusal_is_not_confused_with_unavailable_or_empty(self):
        """Collapsing these three hides the one that needs a schema fix."""
        db = _mongo_db([{"id": "m1"}])
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=_decision("postgres"))), \
             patch("db_postgres.repos.documents_repo.list_documents", new=AsyncMock(return_value=[])):
            empty = await documents_store.read_documents("13195", visibility=VIS)
        assert empty["source"] == "mongo_fallback_pg_empty"
        assert empty["pg_refused_reason"] is None

    @pytest.mark.asyncio
    async def test_read_documents_requires_visibility(self):
        import inspect

        param = inspect.signature(documents_store.read_documents).parameters["visibility"]
        assert param.default is inspect.Parameter.empty


class TestMirrorControl:
    """mirror_to_mongo must actually control the mirror, not merely describe it.

    Audit finding 2026-08-29: the property was defined, documented as the control, and
    read by nothing — write_document mirrored unconditionally. A property that claims
    to gate behaviour and does not is worse than no property, because the next reader
    turns it off and nothing happens.
    """

    COMMON = dict(
        title="T", original_filename="f.pdf", mime_type="application/pdf",
        storage_key="k", file_size_bytes=1,
    )

    @pytest.mark.asyncio
    async def test_mirror_is_skipped_when_the_domain_is_decommissioned(self):
        db = _mongo_db()
        decision = _NoMirror(
            domain="documents", building_id="13195", operation="write",
            source="postgres", shadow_enabled=False, blocked_reason=None,
        )
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=decision)), \
             patch("db_postgres.repos.documents_repo.create_document",
                   new=AsyncMock(return_value={"id": "p1"})):
            result = await documents_store.write_document("13195", **self.COMMON)
        assert result["postgres_written"] is True
        assert result["mongo_written"] is False
        db.documents.update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_mongo_primary_write_is_never_skippable(self):
        """For a Mongo-primary domain the Mongo write is the AUTHORITATIVE one."""
        db = _mongo_db()
        decision = _NoMirror(
            domain="documents", building_id="13195", operation="write",
            source="mongo", shadow_enabled=False, blocked_reason=None,
        )
        with patch.dict("sys.modules", {"database": MagicMock(db=db)}), \
             patch.object(documents_store, "resolve_store", new=AsyncMock(return_value=decision)):
            result = await documents_store.write_document("13195", **self.COMMON)
        assert result["mongo_written"] is True
        db.documents.update_one.assert_awaited_once()
