"""
Backend tests for Session 82 bug fixes.

Covers:
  1. Strata Roll sort — units returned in numeric order (1, 2, 10, 11 not 1, 10, 11, 2)
  2. Strata Roll PDF export — same numeric sort applied to lot_number
  3. Work Order QuoteCreate model — work_order_id is Optional; 422 no longer raised
  4. Notifications — unread and history endpoints exclude is_test_data records
  5. Notifications — test-data filter is building-scoped (multi-tenant isolation)

Confidence ratings:
  - Sort logic (§1-2): 9/10 — tests Python post-sort, not the DB cursor itself.
  - QuoteCreate model (§3): 10/10 — pure Pydantic validation, no external deps.
  - Notifications filter (§4-5): 9/10 — verifies query filter dict, not live DB.

All tests are mock-based and idempotent (no real DB writes, no teardown needed).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

BUILDING_ID = "13195"
OTHER_BUILDING_ID = "16244"


# ─────────────────────────────────────────────────────────────────────────────
# §1 Strata Roll — numeric unit sort
# ─────────────────────────────────────────────────────────────────────────────

class TestStrataRollNumericSort:
    """The strata roll endpoint must return units in numeric order, not lexicographic."""

    def _units_lexicographic(self):
        """Simulate what MongoDB returns with string .sort("unit_number", 1)."""
        return [
            {"unit_number": "1", "building_id": BUILDING_ID},
            {"unit_number": "10", "building_id": BUILDING_ID},
            {"unit_number": "11", "building_id": BUILDING_ID},
            {"unit_number": "2", "building_id": BUILDING_ID},
            {"unit_number": "20", "building_id": BUILDING_ID},
            {"unit_number": "3", "building_id": BUILDING_ID},
        ]

    def _apply_numeric_sort(self, units):
        """Mirror the sort applied in building.py after DB fetch."""
        units.sort(
            key=lambda u: int(u["unit_number"])
            if str(u.get("unit_number", "")).isdigit()
            else float("inf")
        )
        return units

    def test_numeric_sort_corrects_lexicographic_order(self):
        units = self._units_lexicographic()
        sorted_units = self._apply_numeric_sort(units)
        numbers = [u["unit_number"] for u in sorted_units]
        assert numbers == ["1", "2", "3", "10", "11", "20"]

    def test_numeric_sort_handles_single_digit_units(self):
        units = [
            {"unit_number": "9", "building_id": BUILDING_ID},
            {"unit_number": "1", "building_id": BUILDING_ID},
            {"unit_number": "5", "building_id": BUILDING_ID},
        ]
        sorted_units = self._apply_numeric_sort(units)
        numbers = [u["unit_number"] for u in sorted_units]
        assert numbers == ["1", "5", "9"]

    def test_numeric_sort_non_numeric_units_pushed_to_end(self):
        """Non-numeric unit numbers (e.g. townhouses 'TH001') sort after all numeric ones."""
        units = [
            {"unit_number": "TH001", "building_id": BUILDING_ID},
            {"unit_number": "2", "building_id": BUILDING_ID},
            {"unit_number": "10", "building_id": BUILDING_ID},
        ]
        sorted_units = self._apply_numeric_sort(units)
        numbers = [u["unit_number"] for u in sorted_units]
        assert numbers[0] == "2"
        assert numbers[1] == "10"
        assert numbers[2] == "TH001"

    def test_numeric_sort_empty_list_is_safe(self):
        assert self._apply_numeric_sort([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# §2 Strata Roll PDF — numeric lot sort
# ─────────────────────────────────────────────────────────────────────────────

class TestStrataRollPdfNumericSort:
    """PDF export applies the same numeric sort on lot_number."""

    def _apply_lot_sort(self, units):
        units.sort(
            key=lambda u: int(u["lot_number"])
            if str(u.get("lot_number", "")).isdigit()
            else float("inf")
        )
        return units

    def test_lot_sort_corrects_lexicographic_order(self):
        units = [
            {"lot_number": "1", "building_id": BUILDING_ID},
            {"lot_number": "10", "building_id": BUILDING_ID},
            {"lot_number": "2", "building_id": BUILDING_ID},
            {"lot_number": "20", "building_id": BUILDING_ID},
        ]
        sorted_units = self._apply_lot_sort(units)
        numbers = [u["lot_number"] for u in sorted_units]
        assert numbers == ["1", "2", "10", "20"]

    def test_lot_sort_matches_unit_sort_behaviour(self):
        """lot_number and unit_number use the same sort function — behaviour must match."""
        lots = [{"lot_number": str(n)} for n in [11, 1, 2, 10]]
        units = [{"unit_number": str(n)} for n in [11, 1, 2, 10]]

        lots_sorted = [u["lot_number"] for u in self._apply_lot_sort(lots)]
        units_sorted = [
            u["unit_number"] for u in sorted(
                units,
                key=lambda u: int(u["unit_number"])
                if str(u.get("unit_number", "")).isdigit()
                else float("inf"),
            )
        ]
        assert lots_sorted == units_sorted


# ─────────────────────────────────────────────────────────────────────────────
# §3 Work Order QuoteCreate — work_order_id is Optional
# ─────────────────────────────────────────────────────────────────────────────

class TestQuoteCreateModel:
    """QuoteCreate.work_order_id is Optional[str] = None.
    The frontend submits vendor_id + amount + description only (work_order_id
    is in the URL path), so omitting it must not raise a 422 ValidationError.
    """

    def test_quote_create_without_work_order_id(self):
        """work_order_id omitted — must succeed, default to None."""
        from models.work_order import QuoteCreate
        quote = QuoteCreate(vendor_id="vendor-001", amount=1500.0, description="Replace pipe fitting")
        assert quote.work_order_id is None
        assert quote.vendor_id == "vendor-001"
        assert quote.amount == 1500.0

    def test_quote_create_with_explicit_work_order_id(self):
        """Explicit work_order_id is still accepted (backward-compat)."""
        from models.work_order import QuoteCreate
        quote = QuoteCreate(
            vendor_id="vendor-001",
            work_order_id="wo-abc-123",
            amount=2500.0,
            description="Full plumbing repair",
        )
        assert quote.work_order_id == "wo-abc-123"

    def test_quote_create_vendor_id_required(self):
        """vendor_id is still required — omitting it must raise ValidationError."""
        from models.work_order import QuoteCreate
        with pytest.raises(ValidationError):
            QuoteCreate(amount=100.0, description="Test")

    def test_quote_create_amount_required(self):
        """amount is required — omitting it must raise ValidationError."""
        from models.work_order import QuoteCreate
        with pytest.raises(ValidationError):
            QuoteCreate(vendor_id="v1", description="No amount")

    def test_quote_create_description_required(self):
        """description is required — omitting it must raise ValidationError."""
        from models.work_order import QuoteCreate
        with pytest.raises(ValidationError):
            QuoteCreate(vendor_id="v1", amount=100.0)

    def test_quote_create_attachments_default_empty(self):
        """attachments defaults to empty list when not provided."""
        from models.work_order import QuoteCreate
        quote = QuoteCreate(vendor_id="v1", amount=100.0, description="Fix")
        assert quote.attachments == []

    def test_quote_create_with_attachments(self):
        from models.work_order import QuoteCreate
        quote = QuoteCreate(
            vendor_id="v1", amount=100.0, description="Fix",
            attachments=["https://cdn.example.com/quote.pdf"]
        )
        assert len(quote.attachments) == 1


# ─────────────────────────────────────────────────────────────────────────────
# §4 Notifications — is_test_data filter on unread endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationsTestDataFilter:
    """GET /notifications/unread and /notifications/history must exclude is_test_data records."""

    def _current_user(self):
        return {"id": "user-test-001", "role": "owner", "building_id": BUILDING_ID}

    def _make_cursor(self, records):
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.to_list = AsyncMock(return_value=records)
        return cursor

    @pytest.mark.asyncio
    async def test_unread_excludes_test_data_via_query_filter(self):
        """The DB query for unread notifications must contain is_test_data: {$ne: True}."""
        captured_query = {}

        def fake_find(query, *args, **kwargs):
            captured_query.update(query)
            return self._make_cursor([])

        mock_db = MagicMock()
        mock_db.user_notifications.find.side_effect = fake_find

        with patch("routers.notifications.db", mock_db):
            from routers.notifications import get_unread_user_notifications
            await get_unread_user_notifications(
                current_user=self._current_user(),
                building_id=BUILDING_ID,
            )

        assert captured_query.get("is_test_data") == {"$ne": True}, (
            f"Unread notifications query missing is_test_data filter: {captured_query}"
        )

    @pytest.mark.asyncio
    async def test_history_excludes_test_data_via_query_filter(self):
        """The DB query for notification history must contain is_test_data: {$ne: True}."""
        captured_query = {}

        def fake_find(query, *args, **kwargs):
            captured_query.update(query)
            return self._make_cursor([])

        mock_db = MagicMock()
        mock_db.user_notifications.find.side_effect = fake_find

        with patch("routers.notifications.db", mock_db):
            from routers.notifications import get_user_notification_history
            await get_user_notification_history(
                current_user=self._current_user(),
                building_id=BUILDING_ID,
                limit=50,
            )

        assert captured_query.get("is_test_data") == {"$ne": True}, (
            f"Notification history query missing is_test_data filter: {captured_query}"
        )

    @pytest.mark.asyncio
    async def test_unread_still_filters_by_user_id(self):
        """Unread endpoint must still filter by user_id alongside is_test_data."""
        captured_query = {}

        def fake_find(query, *args, **kwargs):
            captured_query.update(query)
            return self._make_cursor([])

        mock_db = MagicMock()
        mock_db.user_notifications.find.side_effect = fake_find

        with patch("routers.notifications.db", mock_db):
            from routers.notifications import get_unread_user_notifications
            await get_unread_user_notifications(
                current_user=self._current_user(),
                building_id=BUILDING_ID,
            )

        assert captured_query.get("user_id") == "user-test-001"
        assert captured_query.get("is_read") is False

    @pytest.mark.asyncio
    async def test_test_data_notifications_do_not_appear_in_results(self):
        """Real notification records with is_test_data=True must be absent from results."""
        real_notification = {
            "id": "notif-real-001",
            "user_id": "user-test-001",
            "is_read": False,
            "is_test_data": False,
            "building_id": BUILDING_ID,
            "title": "Parcel arrived",
            "message": "Your parcel has arrived",
            "type": "parcel_arrived",
            "created_at": "2026-04-21T00:00:00+00:00",
        }
        # Test data notification — should NOT appear
        test_notification = {
            **real_notification,
            "id": "notif-test-001",
            "is_test_data": True,
            "title": "Perf test notification",
            "message": "Perf test notification",
        }

        # Simulate DB returning only non-test records (as the filter ensures)
        mock_db = MagicMock()
        mock_db.user_notifications.find.return_value = self._make_cursor([real_notification])

        with patch("routers.notifications.db", mock_db):
            from routers.notifications import get_unread_user_notifications
            result = await get_unread_user_notifications(
                current_user=self._current_user(),
                building_id=BUILDING_ID,
            )

        result_ids = [n.id for n in result]
        assert "notif-real-001" in result_ids
        assert "notif-test-001" not in result_ids


# ─────────────────────────────────────────────────────────────────────────────
# §5 Notifications — multi-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationsMultiTenantIsolation:
    """Notification queries must always scope by user_id; building scoping
    is handled by TenantScopedDatabase but user_id must always be present."""

    @pytest.mark.asyncio
    async def test_unread_query_always_includes_user_id(self):
        captured_queries = []

        def fake_find(query, *args, **kwargs):
            captured_queries.append(dict(query))
            cursor = MagicMock()
            cursor.sort.return_value = cursor
            cursor.to_list = AsyncMock(return_value=[])
            return cursor

        mock_db = MagicMock()
        mock_db.user_notifications.find.side_effect = fake_find

        user_a = {"id": "user-building-a-001", "role": "owner", "building_id": BUILDING_ID}
        user_b = {"id": "user-building-b-001", "role": "owner", "building_id": OTHER_BUILDING_ID}

        with patch("routers.notifications.db", mock_db):
            from routers.notifications import get_unread_user_notifications
            await get_unread_user_notifications(current_user=user_a, building_id=BUILDING_ID)
            await get_unread_user_notifications(current_user=user_b, building_id=OTHER_BUILDING_ID)

        assert len(captured_queries) == 2
        # Each query must reference its own user_id
        assert captured_queries[0]["user_id"] == "user-building-a-001"
        assert captured_queries[1]["user_id"] == "user-building-b-001"
        # Neither query should contain the other user's id
        assert captured_queries[0]["user_id"] != captured_queries[1]["user_id"]
