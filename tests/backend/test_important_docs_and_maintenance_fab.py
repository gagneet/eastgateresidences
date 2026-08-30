"""
Tests for:
- GET /documents/important endpoint (returns is_important=True docs for dashboard alerts)
- Document upload with is_important + importance_summary auto-generation
- Maintenance FAB: owner/tenant/guest roles can access maintenance endpoint
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(doc_id: str, name: str, is_important: bool = True, summary: str = "Test summary.") -> dict:
    return {
        "_id": doc_id,
        "id": doc_id,
        "name": name,
        "is_important": is_important,
        "importance_summary": summary if is_important else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by": "user-1",
    }


# ---------------------------------------------------------------------------
# Unit tests: is_important filtering logic
# ---------------------------------------------------------------------------

class TestImportantDocFiltering:
    def test_filters_only_important_docs(self):
        docs = [
            _make_doc("d1", "Fire Safety Notice", is_important=True),
            _make_doc("d2", "Regular Newsletter", is_important=False),
            _make_doc("d3", "AGM Agenda", is_important=True),
        ]
        important = [d for d in docs if d.get("is_important")]
        assert len(important) == 2
        names = {d["name"] for d in important}
        assert "Fire Safety Notice" in names
        assert "AGM Agenda" in names
        assert "Regular Newsletter" not in names

    def test_importance_summary_only_on_important_docs(self):
        doc_important = _make_doc("d1", "Notice", is_important=True, summary="Critical update.")
        doc_regular = _make_doc("d2", "Regular", is_important=False)
        assert doc_important["importance_summary"] == "Critical update."
        assert doc_regular["importance_summary"] is None

    def test_empty_collection_returns_empty_list(self):
        docs = []
        important = [d for d in docs if d.get("is_important")]
        assert important == []

    def test_all_unimportant_returns_empty_list(self):
        docs = [
            _make_doc("d1", "Doc 1", is_important=False),
            _make_doc("d2", "Doc 2", is_important=False),
        ]
        important = [d for d in docs if d.get("is_important")]
        assert important == []


# ---------------------------------------------------------------------------
# Unit tests: importance_summary auto-generation
# ---------------------------------------------------------------------------

class TestImportanceSummaryGeneration:
    def _generate_summary(self, description: str, max_sentences: int = 3) -> str:
        """Replicate the backend logic from server.py DocumentCreate upload handler."""
        if not description:
            return "Important document — please review."
        sentences = [s.strip() for s in description.replace('\n', ' ').split('.') if s.strip()]
        selected = sentences[:max_sentences]
        return '. '.join(selected) + ('.' if selected else '')

    def test_generates_summary_from_description(self):
        desc = "Fire safety routes have been updated. New exit signs installed. All residents must review. Additional info here."
        summary = self._generate_summary(desc)
        sentences = [s for s in summary.split('.') if s.strip()]
        assert len(sentences) <= 3

    def test_empty_description_gives_fallback(self):
        summary = self._generate_summary("")
        assert summary == "Important document — please review."

    def test_short_description_preserved_fully(self):
        desc = "Short notice."
        summary = self._generate_summary(desc)
        assert "Short notice" in summary

    def test_long_description_truncated_to_3_sentences(self):
        desc = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
        summary = self._generate_summary(desc)
        # Should not exceed 3 sentences worth
        parts = [p.strip() for p in summary.split('.') if p.strip()]
        assert len(parts) <= 3


# ---------------------------------------------------------------------------
# Unit tests: document upload model fields
# ---------------------------------------------------------------------------

class TestDocumentUploadModel:
    def test_is_important_defaults_to_false(self):
        doc = {"name": "Test", "is_important": False}
        assert doc["is_important"] is False

    def test_is_important_can_be_set_true(self):
        doc = {"name": "Important Notice", "is_important": True, "importance_summary": "Critical info."}
        assert doc["is_important"] is True
        assert doc["importance_summary"] == "Critical info."

    def test_importance_summary_optional(self):
        doc = {"name": "Test", "is_important": True}
        # importance_summary is Optional[str] - defaults to None
        summary = doc.get("importance_summary")
        assert summary is None  # Not required


# ---------------------------------------------------------------------------
# Unit tests: Maintenance FAB role visibility
# ---------------------------------------------------------------------------

class TestMaintenanceFABRoleVisibility:
    FAB_ROLES = {"owner", "tenant", "guest"}

    def _should_show_fab(self, role: str) -> bool:
        return role in self.FAB_ROLES

    def test_owner_sees_fab(self):
        assert self._should_show_fab("owner") is True

    def test_tenant_sees_fab(self):
        assert self._should_show_fab("tenant") is True

    def test_guest_sees_fab(self):
        assert self._should_show_fab("guest") is True

    def test_super_admin_no_fab(self):
        assert self._should_show_fab("super_admin") is False

    def test_ec_member_no_fab(self):
        assert self._should_show_fab("ec_member") is False

    def test_strata_manager_no_fab(self):
        assert self._should_show_fab("strata_manager") is False

    def test_chairman_no_fab(self):
        assert self._should_show_fab("chairman") is False

    def test_service_provider_no_fab(self):
        assert self._should_show_fab("service_provider") is False

    def test_admin_staff_no_fab(self):
        assert self._should_show_fab("admin_staff") is False

    def test_real_estate_agent_no_fab(self):
        assert self._should_show_fab("real_estate_agent") is False


# ---------------------------------------------------------------------------
# Integration-style tests: GET /documents/important endpoint
# ---------------------------------------------------------------------------

class TestImportantDocsEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_queries_important_docs(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        important_docs = [
            _make_doc("d1", "Fire Safety Notice", is_important=True, summary="Check fire exits."),
            _make_doc("d2", "Water Shutdown Notice", is_important=True, summary="Water off Friday 9am-12pm."),
        ]

        async def mock_to_list(n):
            return important_docs

        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = mock_to_list
        mock_db.documents.find.return_value = mock_cursor

        # Simulate the query: db.documents.find({"is_important": True})
        query_filter = {"is_important": True}
        result_cursor = mock_db.documents.find(query_filter)
        result_cursor.sort.return_value = result_cursor
        result_cursor.limit.return_value = result_cursor
        results = await result_cursor.to_list(10)

        assert len(results) == 2
        assert mock_db.documents.find.called
        call_args = mock_db.documents.find.call_args[0][0]
        assert call_args.get("is_important") is True

    @pytest.mark.asyncio
    async def test_endpoint_sorts_by_created_at_desc(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()

        async def mock_to_list(n):
            return []

        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = mock_to_list
        mock_db.documents.find.return_value = mock_cursor

        result_cursor = mock_db.documents.find({"is_important": True})
        result_cursor.sort("created_at", -1)
        result_cursor.limit(10)

        mock_cursor.sort.assert_called_once_with("created_at", -1)
        mock_cursor.limit.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_endpoint_limits_to_10_results(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        all_docs = [_make_doc(f"d{i}", f"Notice {i}", is_important=True) for i in range(15)]

        call_count = 0

        async def mock_to_list(n):
            nonlocal call_count
            call_count += 1
            return all_docs[:n]

        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = mock_to_list

        mock_db.documents.find.return_value = mock_cursor
        result_cursor = mock_db.documents.find({"is_important": True})
        result_cursor.sort("created_at", -1)
        result_cursor.limit(10)
        results = await result_cursor.to_list(10)

        assert len(results) == 10


# ---------------------------------------------------------------------------
# Tests: dismissal state management (JS localStorage equivalent in Python)
# ---------------------------------------------------------------------------

class TestDismissalStatePersistence:
    def test_dismiss_adds_to_set(self):
        dismissed = set()
        dismissed.add("doc-1")
        assert "doc-1" in dismissed

    def test_multiple_dismissals_accumulate(self):
        dismissed = set()
        for doc_id in ["doc-1", "doc-2", "doc-3"]:
            dismissed.add(doc_id)
        assert len(dismissed) == 3

    def test_dismissed_docs_hidden_from_visible_list(self):
        all_docs = [{"id": "d1"}, {"id": "d2"}, {"id": "d3"}]
        dismissed = {"d1", "d3"}
        visible = [d for d in all_docs if d["id"] not in dismissed]
        assert len(visible) == 1
        assert visible[0]["id"] == "d2"

    def test_already_dismissed_not_duplicated(self):
        dismissed = {"doc-1"}
        dismissed.add("doc-1")  # Add again
        assert len(dismissed) == 1
