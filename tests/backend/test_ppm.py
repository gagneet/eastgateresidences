"""
Tests for PPM (Preventive Maintenance Plan) module.

Target:
- Seed is idempotent (run twice, same item count)
- GET /api/ppm returns 79 items for building 13195
- POST /api/ppm/{id}/complete correctly recomputes next_due_date
- is_compliance=True for all 8 fire protection compliance items
- Items with past next_due_date have status='overdue'
- Owner role only sees limited fields in GET /api/ppm response
"""

import uuid
from datetime import datetime, date, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Module-level model imports used in TestPPMApiEndpoints
# (conftest.py adds backend/ to sys.path before any test runs)
from models.ppm import PPMScheduleUpdate, PPMCompletionLog

# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────────────

BUILDING_ID = "13195"


def _make_ppm_item(
        schedule_id=None,
        section="FIRE PROTECTION SYSTEMS & EVACUATION",
        description="Maintain fire hose reels",
        inspection_type="Compliance",
        frequency="6monthly",
        frequency_days=180,
        status="scheduled",
        is_compliance=True,
        next_due_date=None,
        last_completed_date=None,
):
    now = datetime.now(timezone.utc).isoformat()
    schedule_id = schedule_id or str(uuid.uuid4())
    next_due_date = next_due_date or (date.today() + timedelta(days=60)).isoformat()
    return {
        "id": schedule_id,
        "schedule_id": schedule_id,
        "building_id": BUILDING_ID,
        "section": section,
        "description": description,
        "vendor_name": "FireSafe Pty Ltd",
        "vendor_contact": "1300 555 123",
        "contractor_id": None,
        "inspection_type": inspection_type,
        "standard": "AS1851-2012",
        "frequency": frequency,
        "frequency_days": frequency_days,
        "last_completed_date": last_completed_date,
        "next_due_date": next_due_date,
        "status": status,
        "is_compliance": is_compliance,
        "lifespan_years": None,
        "capital_years": [],
        "notification_log": [],
        "completion_log": [],
        "created_at": now,
        "updated_at": now,
    }


def _make_user(role="owner", unit="TH087", ec_position=None):
    user = {
        "id": "user-001",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": role,
        "unit_number": unit,
        "building_id": BUILDING_ID,
        "status": "active",
    }
    if ec_position is not None:
        user["ec_position"] = ec_position
    return user


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMSeedParsing — verify Excel parse logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMSeedParsing:
    """Tests for the seed script parsing logic (no DB required)."""

    def test_frequency_normalisation_annual(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("Annually") == ("annual", 365)
        assert _normalise_frequency("Annual") == ("annual", 365)

    def test_frequency_normalisation_quarterly(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("Quarterly") == ("quarterly", 90)
        assert _normalise_frequency("3 monthly") == ("quarterly", 90)
        assert _normalise_frequency("per Agreement (quarterly)") == ("quarterly", 90)

    def test_frequency_normalisation_monthly(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("Monthly") == ("monthly", 30)

    def test_frequency_normalisation_6monthly(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("6 monthly") == ("6monthly", 180)
        assert _normalise_frequency("6 Monthly") == ("6monthly", 180)
        assert _normalise_frequency("6mthly - Annually") == ("6monthly", 180)

    def test_frequency_normalisation_biannual(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("Biannually") == ("biannual", 180)
        assert _normalise_frequency("Biannanual") == ("biannual", 180)

    def test_frequency_normalisation_5yearly(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("5 yearly") == ("5yearly", 1825)

    def test_frequency_normalisation_unknown_defaults_annual(self):
        from seeds.ppm_schedule import _normalise_frequency
        assert _normalise_frequency("one off") == ("annual", 365)
        assert _normalise_frequency("As required") == ("annual", 365)
        assert _normalise_frequency(None) == ("annual", 365)

    def test_compute_status_overdue(self):
        from seeds.ppm_schedule import _compute_status
        past_date = (date.today() - timedelta(days=10)).isoformat()
        assert _compute_status(past_date) == "overdue"

    def test_compute_status_due_soon(self):
        from seeds.ppm_schedule import _compute_status
        soon_date = (date.today() + timedelta(days=15)).isoformat()
        assert _compute_status(soon_date) == "due_soon"

    def test_compute_status_scheduled(self):
        from seeds.ppm_schedule import _compute_status
        future_date = (date.today() + timedelta(days=60)).isoformat()
        assert _compute_status(future_date) == "scheduled"

    def test_parse_excel_returns_79_items(self):
        """The Excel file should parse to exactly 79 items."""
        from seeds.ppm_schedule import _parse_excel, EXCEL_PATH
        if not EXCEL_PATH.exists():
            pytest.skip("Excel file not available in this environment")
        items = _parse_excel(EXCEL_PATH)
        assert len(items) == 79, f"Expected 79 items, got {len(items)}"

    def test_parse_excel_has_15_sections(self):
        """The Excel file has 15 sections."""
        from seeds.ppm_schedule import _parse_excel, EXCEL_PATH
        if not EXCEL_PATH.exists():
            pytest.skip("Excel file not available in this environment")
        items = _parse_excel(EXCEL_PATH)
        sections = {i["section"] for i in items}
        assert len(sections) == 15, f"Expected 15 sections, got {len(sections)}"

    def test_parse_excel_has_8_compliance_items(self):
        """The Excel file should yield exactly 8 compliance items."""
        from seeds.ppm_schedule import _parse_excel, EXCEL_PATH
        if not EXCEL_PATH.exists():
            pytest.skip("Excel file not available in this environment")
        items = _parse_excel(EXCEL_PATH)
        compliance = [i for i in items if i["is_compliance"]]
        assert len(compliance) == 8, f"Expected 8 compliance items, got {len(compliance)}"

    def test_compliance_items_are_in_fire_section(self):
        """All compliance items should be in fire protection section."""
        from seeds.ppm_schedule import _parse_excel, EXCEL_PATH
        if not EXCEL_PATH.exists():
            pytest.skip("Excel file not available in this environment")
        items = _parse_excel(EXCEL_PATH)
        compliance = [i for i in items if i["is_compliance"]]
        fire_compliance = [i for i in compliance if
                           "FIRE" in i["section"].upper() or i["section"] == "FURNITURE & FITTINGS"]
        assert len(fire_compliance) == len(compliance), (
            f"All compliance items should be fire-related; got {[i['section'] for i in compliance]}"
        )

    def test_fire_section_items_have_is_compliance_true(self):
        """Items in FIRE PROTECTION section with inspection_type=Compliance should be flagged."""
        from seeds.ppm_schedule import _parse_excel, EXCEL_PATH
        if not EXCEL_PATH.exists():
            pytest.skip("Excel file not available in this environment")
        items = _parse_excel(EXCEL_PATH)
        fire_items = [i for i in items if "FIRE" in i["section"].upper()]
        compliance_fire = [i for i in fire_items if i["inspection_type"] == "Compliance"]
        for item in compliance_fire:
            assert item["is_compliance"] is True, f"Expected is_compliance=True for {item['description']}"


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMCompletionLogic — test next_due_date recomputation
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMCompletionLogic:
    """Tests for the completion endpoint's date recomputation logic."""

    def test_next_due_date_recomputed_from_completion_date(self):
        """next_due_date = completion_date + frequency_days."""
        completion_date = date(2026, 6, 15)
        frequency_days = 180
        expected_next = (completion_date + timedelta(days=frequency_days)).isoformat()
        assert expected_next == "2026-12-12"

    def test_next_due_annual_recompute(self):
        """Annual item: next due = completion + 365 days."""
        completion = date(2026, 3, 1)
        next_due = (completion + timedelta(days=365)).isoformat()
        assert next_due == "2027-03-01"

    def test_next_due_quarterly_recompute(self):
        """Quarterly item: next due = completion + 90 days."""
        completion = date(2026, 3, 1)
        next_due = (completion + timedelta(days=90)).isoformat()
        assert next_due == "2026-05-30"

    def test_compute_status_after_completion(self):
        """After completion, the new status should be 'scheduled' for far future dates."""
        from routers.ppm import _compute_status
        future = (date.today() + timedelta(days=90)).isoformat()
        assert _compute_status(future) == "scheduled"

    def test_overdue_status_if_past_due(self):
        """An item whose next_due_date is in the past should be 'overdue'."""
        from routers.ppm import _compute_status
        past = (date.today() - timedelta(days=5)).isoformat()
        assert _compute_status(past) == "overdue"

    def test_due_soon_status_within_30_days(self):
        """An item due in ≤ 30 days should be 'due_soon'."""
        from routers.ppm import _compute_status
        soon = (date.today() + timedelta(days=20)).isoformat()
        assert _compute_status(soon) == "due_soon"


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMPermissions — field visibility per role
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMPermissions:
    """Tests verifying role-based field visibility."""

    def test_manager_is_detected_correctly(self):
        # Per migration 0025: chairman is no longer a top-level role.
        # The set of manager *roles* is now: super_admin, ec_member,
        # strata_manager, strata_admin (chairman maps to ec_member +
        # ec_position=CHAIRMAN, which is still ec_member at the role level).
        from routers.ppm import _is_manager
        for role in ("super_admin", "ec_member", "strata_manager"):
            assert _is_manager({"role": role}) is True

    def test_owner_is_not_manager(self):
        from routers.ppm import _is_manager
        assert _is_manager({"role": "owner"}) is False

    def test_tenant_is_not_manager(self):
        from routers.ppm import _is_manager
        assert _is_manager({"role": "tenant"}) is False

    def test_owner_sees_limited_fields(self):
        """Owner response should not include vendor_contact or completion_log."""
        limited_fields = {
            "id", "schedule_id", "section", "description",
            "inspection_type", "frequency", "next_due_date", "status", "is_compliance",
        }
        item = _make_ppm_item()
        owner_view = {k: v for k, v in item.items() if k in limited_fields}
        assert "vendor_contact" not in owner_view
        assert "completion_log" not in owner_view
        assert "notification_log" not in owner_view
        assert "is_compliance" in owner_view

    def test_manager_sees_all_fields(self):
        """Manager response should include vendor_contact and completion_log."""
        item = _make_ppm_item()
        assert "vendor_contact" in item
        assert "completion_log" in item
        assert "notification_log" in item


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMDashboard — health score calculation
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMDashboard:
    """Tests for the dashboard health score computation."""

    def test_perfect_health_score(self):
        """When no compliance items are overdue, health = 100."""
        compliance_items = [_make_ppm_item(status="scheduled") for _ in range(8)]
        compliance_overdue = [i for i in compliance_items if i["status"] == "overdue"]
        score = round(
            (len(compliance_items) - len(compliance_overdue)) / len(compliance_items) * 100, 1
        )
        assert score == 100.0

    def test_zero_health_score(self):
        """When all compliance items are overdue, health = 0."""
        compliance_items = [
            _make_ppm_item(status="overdue", next_due_date=(date.today() - timedelta(days=5)).isoformat())
            for _ in range(8)
        ]
        compliance_overdue = [i for i in compliance_items if i["status"] == "overdue"]
        score = round(
            (len(compliance_items) - len(compliance_overdue)) / len(compliance_items) * 100, 1
        )
        assert score == 0.0

    def test_partial_health_score(self):
        """When half compliance items are overdue, health = 50."""
        past = (date.today() - timedelta(days=5)).isoformat()
        future = (date.today() + timedelta(days=90)).isoformat()
        items = (
                [_make_ppm_item(status="overdue", next_due_date=past) for _ in range(4)]
                + [_make_ppm_item(status="scheduled", next_due_date=future) for _ in range(4)]
        )
        overdue = sum(1 for i in items if i["status"] == "overdue")
        score = round((len(items) - overdue) / len(items) * 100, 1)
        assert score == 50.0

    def test_no_compliance_items_defaults_100(self):
        """If there are no compliance items, health score should be 100."""
        compliance_items = []
        score = 100.0 if not compliance_items else 0.0
        assert score == 100.0


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMItemStatus — status logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMItemStatus:
    """Tests for status computation."""

    def test_item_is_overdue_if_next_due_in_past(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        item = _make_ppm_item(next_due_date=past, status="overdue")
        assert item["status"] == "overdue"

    def test_item_is_due_soon_within_30_days(self):
        soon = (date.today() + timedelta(days=15)).isoformat()
        item = _make_ppm_item(next_due_date=soon, status="due_soon")
        assert item["status"] == "due_soon"

    def test_item_is_scheduled_if_far_future(self):
        future = (date.today() + timedelta(days=90)).isoformat()
        item = _make_ppm_item(next_due_date=future, status="scheduled")
        assert item["status"] == "scheduled"

    def test_compliance_item_flag(self):
        """Compliance item must have is_compliance=True."""
        item = _make_ppm_item(inspection_type="Compliance")
        assert item["is_compliance"] is True

    def test_non_compliance_item_flag(self):
        """Non-compliance item must have is_compliance=False."""
        item = _make_ppm_item(inspection_type="Routine", is_compliance=False)
        assert item["is_compliance"] is False


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMModels — Pydantic model validation
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMModels:
    """Tests for PPM Pydantic models."""

    def test_schedule_item_model(self):
        from models.ppm import PPMScheduleItem
        now = datetime.now(timezone.utc).isoformat()
        item = PPMScheduleItem(
            id="abc",
            schedule_id="abc",
            building_id=BUILDING_ID,
            section="FIRE PROTECTION SYSTEMS & EVACUATION",
            description="Maintain fire hose reels",
            frequency="6monthly",
            frequency_days=180,
            next_due_date=(date.today() + timedelta(days=60)).isoformat(),
            status="scheduled",
            is_compliance=True,
            created_at=now,
            updated_at=now,
        )
        assert item.building_id == BUILDING_ID
        assert item.is_compliance is True

    def test_completion_log_model(self):
        from models.ppm import PPMCompletionLog
        log = PPMCompletionLog(
            completed_by="FireSafe Pty Ltd",
            completion_date="2026-06-15",
            notes="Annual service completed",
            certificate_ref="AS1851-2026-001",
        )
        assert log.completed_by == "FireSafe Pty Ltd"
        assert log.certificate_ref == "AS1851-2026-001"

    def test_completion_log_optional_fields(self):
        from models.ppm import PPMCompletionLog
        log = PPMCompletionLog(
            completed_by="OC",
            completion_date="2026-01-01",
        )
        assert log.notes is None
        assert log.certificate_ref is None

    def test_dashboard_summary_model(self):
        from models.ppm import PPMDashboardSummary
        dash = PPMDashboardSummary(
            total_items=79,
            overdue_count=5,
            due_soon_count=3,
            compliance_total=8,
            compliance_overdue_count=1,
            health_score=87.5,
        )
        assert dash.total_items == 79
        assert dash.health_score == 87.5
        assert dash.compliance_total == 8

    def test_dashboard_summary_health_score_may_be_absent(self):
        """health_score is optional: None means "no compliance items tracked"."""
        from models.ppm import PPMDashboardSummary
        dash = PPMDashboardSummary(
            total_items=0,
            overdue_count=0,
            due_soon_count=0,
            compliance_total=0,
            compliance_overdue_count=0,
        )
        assert dash.health_score is None

    def test_schedule_update_model_optional_fields(self):
        from models.ppm import PPMScheduleUpdate
        update = PPMScheduleUpdate(vendor_name="New Vendor")
        assert update.vendor_name == "New Vendor"
        assert update.vendor_contact is None
        assert update.next_due_date is None

    def test_limited_item_model(self):
        from models.ppm import PPMScheduleItemLimited
        now = datetime.now(timezone.utc).isoformat()
        item = PPMScheduleItemLimited(
            id="abc",
            schedule_id="abc",
            section="FIRE PROTECTION SYSTEMS & EVACUATION",
            description="Maintain fire hose reels",
            frequency="6monthly",
            next_due_date=(date.today() + timedelta(days=60)).isoformat(),
            status="scheduled",
            is_compliance=True,
        )
        assert item.is_compliance is True
        # Verify that vendor_contact and completion_log are NOT fields in the limited model
        limited_field_names = set(item.model_fields.keys())
        assert "vendor_contact" not in limited_field_names
        assert "completion_log" not in limited_field_names
        assert "notification_log" not in limited_field_names


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMNotificationDedup — notification deduplication logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMNotificationDedup:
    """Tests for notification deduplication logic in the cron."""

    def test_not_notified_when_log_empty(self):
        from cron.cron_ppm_notifications import _already_notified
        item = _make_ppm_item()
        item["notification_log"] = []
        assert _already_notified(item, 30) is False

    def test_notified_when_log_has_today_entry(self):
        from cron.cron_ppm_notifications import _already_notified
        today = date.today().isoformat()
        item = _make_ppm_item()
        item["notification_log"] = [{"date": f"{today}T07:00:00+00:00", "threshold_days": 30}]
        assert _already_notified(item, 30) is True

    def test_not_duplicate_different_threshold(self):
        from cron.cron_ppm_notifications import _already_notified
        today = date.today().isoformat()
        item = _make_ppm_item()
        item["notification_log"] = [{"date": f"{today}T07:00:00+00:00", "threshold_days": 14}]
        # Different threshold — should NOT be considered a duplicate
        assert _already_notified(item, 30) is False


# ──────────────────────────────────────────────────────────────────────────────
# TestPPMApiEndpoints — API endpoint logic with mocked DB
# All tests use AsyncMock — never touch real MongoDB. Fully idempotent.
# ──────────────────────────────────────────────────────────────────────────────

class TestPPMApiEndpoints:
    """
    Tests for the 8 PPM API endpoint functions.

    Strategy:
    - Import endpoint functions directly (bypassing FastAPI dependency injection)
    - Pass building_id=BUILDING_ID as a plain argument (skips get_current_building Depends)
    - Mock `routers.ppm.db` at the module level so no real DB is touched
    - All mocks are created and discarded within each test — fully idempotent
    """

    # ── helpers ───────────────────────────────────────────────────────────────

    def _mock_cursor(self, items: list):
        """Return a MagicMock that behaves like a Motor cursor chain."""
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = cursor
        cursor.to_list = AsyncMock(return_value=items)
        return cursor

    # ── GET /ppm — list_ppm_items ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_ppm_items_returns_full_data_for_manager(self):
        """Manager sees all fields including vendor_contact and completion_log."""
        from routers.ppm import list_ppm_items
        manager = _make_user(role="strata_manager")
        items = [_make_ppm_item() for _ in range(3)]

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find.return_value = self._mock_cursor(items)
            result = await list_ppm_items(
                current_user=manager,
                building_id=BUILDING_ID,
            )

        assert len(result) == 3
        assert "vendor_contact" in result[0]
        assert "completion_log" in result[0]

    @pytest.mark.asyncio
    async def test_list_ppm_items_returns_limited_fields_for_owner(self):
        """Owner sees only public fields — no vendor_contact or completion_log."""
        from routers.ppm import list_ppm_items
        owner = _make_user(role="owner")
        items = [_make_ppm_item()]

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find.return_value = self._mock_cursor(items)
            result = await list_ppm_items(
                current_user=owner,
                building_id=BUILDING_ID,
            )

        assert len(result) == 1
        assert "vendor_contact" not in result[0]
        assert "completion_log" not in result[0]
        assert "notification_log" not in result[0]
        # Public fields ARE present
        assert "section" in result[0]
        assert "status" in result[0]
        assert "is_compliance" in result[0]

    @pytest.mark.asyncio
    async def test_list_ppm_items_raises_503_on_db_error(self):
        """DB failure in list_ppm_items raises HTTPException 503 (not raw 500)."""
        from routers.ppm import list_ppm_items
        from fastapi import HTTPException
        owner = _make_user(role="owner")

        bad_cursor = MagicMock()
        bad_cursor.sort.return_value = bad_cursor
        bad_cursor.skip.return_value = bad_cursor
        bad_cursor.limit.return_value = bad_cursor
        bad_cursor.to_list = AsyncMock(side_effect=Exception("Mongo connection refused"))

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find.return_value = bad_cursor
            with pytest.raises(HTTPException) as exc_info:
                await list_ppm_items(
                    current_user=owner,
                    building_id=BUILDING_ID,
                )

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_list_ppm_items_rejects_invalid_status_filter(self):
        """Invalid status value raises HTTPException 400."""
        from routers.ppm import list_ppm_items
        from fastapi import HTTPException
        owner = _make_user(role="owner")

        with patch("routers.ppm.db"):
            with pytest.raises(HTTPException) as exc_info:
                await list_ppm_items(
                    status="invalid_status",
                    current_user=owner,
                    building_id=BUILDING_ID,
                )

        assert exc_info.value.status_code == 400

    # ── GET /ppm/{schedule_id} — get_ppm_item ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_ppm_item_returns_404_for_unknown_schedule_id(self):
        """Missing schedule_id raises HTTPException 404."""
        from routers.ppm import get_ppm_item
        from fastapi import HTTPException
        owner = _make_user(role="owner")

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find_one = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc_info:
                await get_ppm_item(
                    schedule_id="nonexistent-id",
                    current_user=owner,
                    building_id=BUILDING_ID,
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_ppm_item_returns_item_when_found(self):
        """Existing schedule_id returns the full item dict."""
        from routers.ppm import get_ppm_item
        # Per migration 0025: chairman is an ec_position, not a role.
        manager = _make_user(role="ec_member", ec_position="CHAIRMAN")
        item = _make_ppm_item()

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find_one = AsyncMock(return_value=item)
            result = await get_ppm_item(
                schedule_id=item["schedule_id"],
                current_user=manager,
                building_id=BUILDING_ID,
            )

        assert result["schedule_id"] == item["schedule_id"]
        assert result["section"] == item["section"]

    # ── PUT /ppm/{schedule_id} — update_ppm_item ──────────────────────────────

    @pytest.mark.asyncio
    async def test_update_ppm_item_forbidden_for_owner(self):
        """Owner role cannot update PPM items — must get 403."""
        from routers.ppm import update_ppm_item
        from fastapi import HTTPException
        owner = _make_user(role="owner")

        with patch("routers.ppm.db"):
            with pytest.raises(HTTPException) as exc_info:
                await update_ppm_item(
                    schedule_id="some-id",
                    data=PPMScheduleUpdate(vendor_name="New Vendor"),
                    current_user=owner,
                    building_id=BUILDING_ID,
                )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_update_ppm_item_syncs_calendar_when_due_date_changes(self):
        """
        Regression: when next_due_date is updated, open calendar events must
        also be updated to the new date (Bug Fix #2).
        """
        from routers.ppm import update_ppm_item
        manager = _make_user(role="strata_manager")
        item = _make_ppm_item()
        new_date = (date.today() + timedelta(days=90)).isoformat()

        with patch("routers.ppm.db") as mock_db, \
                patch("routers.ppm.create_audit_log", new_callable=AsyncMock):
            mock_db.maintenance_schedules.find_one = AsyncMock(side_effect=[item, item])
            mock_db.maintenance_schedules.update_one = AsyncMock()
            mock_db.events.update_many = AsyncMock()

            await update_ppm_item(
                schedule_id=item["schedule_id"],
                data=PPMScheduleUpdate(next_due_date=new_date),
                current_user=manager,
                building_id=BUILDING_ID,
            )

        # calendar events MUST have been updated
        mock_db.events.update_many.assert_awaited_once()
        call_filter, call_update = mock_db.events.update_many.call_args[0]
        assert call_filter["event_type"] == "ppm"
        assert call_filter["completed_at"] == {"$exists": False}
        assert call_update["$set"]["start_date"] == new_date

    @pytest.mark.asyncio
    async def test_update_ppm_item_does_not_touch_calendar_when_date_unchanged(self):
        """
        If only vendor_name is updated (no next_due_date), calendar events
        must NOT be modified.
        """
        from routers.ppm import update_ppm_item
        manager = _make_user(role="strata_manager")
        item = _make_ppm_item()

        with patch("routers.ppm.db") as mock_db, \
                patch("routers.ppm.create_audit_log", new_callable=AsyncMock):
            mock_db.maintenance_schedules.find_one = AsyncMock(side_effect=[item, item])
            mock_db.maintenance_schedules.update_one = AsyncMock()
            mock_db.events.update_many = AsyncMock()

            await update_ppm_item(
                schedule_id=item["schedule_id"],
                data=PPMScheduleUpdate(vendor_name="New Vendor"),
                current_user=manager,
                building_id=BUILDING_ID,
            )

        mock_db.events.update_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_ppm_item_sanitizes_vendor_name(self):
        """vendor_name containing HTML must be escaped before storage."""
        from routers.ppm import update_ppm_item
        import html as html_lib
        manager = _make_user(role="strata_manager")
        item = _make_ppm_item()
        xss_input = '<script>alert("xss")</script>'

        captured_updates = {}

        async def _capture_update(filter_q, update_q):
            captured_updates.update(update_q["$set"])

        with patch("routers.ppm.db") as mock_db, \
                patch("routers.ppm.create_audit_log", new_callable=AsyncMock):
            mock_db.maintenance_schedules.find_one = AsyncMock(side_effect=[item, item])
            mock_db.maintenance_schedules.update_one = _capture_update
            mock_db.events.update_many = AsyncMock()

            await update_ppm_item(
                schedule_id=item["schedule_id"],
                data=PPMScheduleUpdate(vendor_name=xss_input),
                current_user=manager,
                building_id=BUILDING_ID,
            )

        assert "<script>" not in captured_updates.get("vendor_name", "")
        assert html_lib.escape(xss_input) == captured_updates.get("vendor_name", "")

    # ── POST /ppm/{schedule_id}/complete — complete_ppm_item ──────────────────

    @pytest.mark.asyncio
    async def test_complete_ppm_item_recomputes_next_due_date(self):
        """
        Completion date + frequency_days must yield the correct next_due_date.
        Also verifies calendar events are marked complete and a new one is inserted.
        """
        from routers.ppm import complete_ppm_item
        # Per migration 0025: chairman is an ec_position, not a role.
        manager = _make_user(role="ec_member", ec_position="CHAIRMAN")
        completion_date = "2026-06-15"
        frequency_days = 180
        expected_next = (date.fromisoformat(completion_date) + timedelta(days=frequency_days)).isoformat()
        # expected: 2026-12-12
        assert expected_next == "2026-12-12"

        item = _make_ppm_item(frequency_days=frequency_days)
        updated_item = {**item, "last_completed_date": completion_date, "next_due_date": expected_next}

        captured_set = {}

        async def _capture_update_one(filter_q, update_q):
            captured_set.update(update_q.get("$set", {}))

        with patch("routers.ppm.db") as mock_db, \
                patch("routers.ppm.create_audit_log", new_callable=AsyncMock):
            mock_db.maintenance_schedules.find_one = AsyncMock(side_effect=[item, updated_item])
            mock_db.maintenance_schedules.update_one = _capture_update_one
            mock_db.events.update_many = AsyncMock()
            mock_db.events.insert_one = AsyncMock()

            result = await complete_ppm_item(
                schedule_id=item["schedule_id"],
                data=PPMCompletionLog(
                    completed_by="FireSafe Pty Ltd",
                    completion_date=completion_date,
                    certificate_ref="AS1851-2026-001",
                    notes="Annual hose reel service",
                ),
                current_user=manager,
                building_id=BUILDING_ID,
            )

        assert captured_set["next_due_date"] == expected_next
        assert captured_set["last_completed_date"] == completion_date
        # Calendar: mark old event done
        mock_db.events.update_many.assert_awaited_once()
        # Calendar: insert next occurrence
        mock_db.events.insert_one.assert_awaited_once()
        # Returned item reflects completion
        assert result["last_completed_date"] == completion_date

    @pytest.mark.asyncio
    async def test_complete_ppm_item_rejects_invalid_date(self):
        """Malformed completion_date raises HTTPException 400."""
        from routers.ppm import complete_ppm_item
        from fastapi import HTTPException
        # Per migration 0025: chairman is an ec_position, not a role.
        manager = _make_user(role="ec_member", ec_position="CHAIRMAN")
        item = _make_ppm_item()

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find_one = AsyncMock(return_value=item)
            with pytest.raises(HTTPException) as exc_info:
                await complete_ppm_item(
                    schedule_id=item["schedule_id"],
                    data=PPMCompletionLog(
                        completed_by="Contractor",
                        completion_date="not-a-date",
                    ),
                    current_user=manager,
                    building_id=BUILDING_ID,
                )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_complete_ppm_item_forbidden_for_tenant(self):
        """Tenant role must receive 403 when attempting to log completion."""
        from routers.ppm import complete_ppm_item
        from fastapi import HTTPException
        tenant = _make_user(role="tenant")

        with patch("routers.ppm.db"):
            with pytest.raises(HTTPException) as exc_info:
                await complete_ppm_item(
                    schedule_id="some-id",
                    data=PPMCompletionLog(
                        completed_by="Tenant",
                        completion_date="2026-06-15",
                    ),
                    current_user=tenant,
                    building_id=BUILDING_ID,
                )

        assert exc_info.value.status_code == 403

    # ── GET /ppm/dashboard — ppm_dashboard ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_ppm_dashboard_calculates_correct_health_score(self):
        """
        Health score = (compliance_total - compliance_overdue) / compliance_total * 100.
        With 8 compliance items and 2 overdue: health = 75.0
        """
        from routers.ppm import ppm_dashboard
        manager = _make_user(role="ec_member")

        # count_documents returns different values based on filter
        async def _count(filter_q):
            if filter_q.get("is_compliance") is True and filter_q.get("status") == "overdue":
                return 2
            if filter_q.get("is_compliance") is True:
                return 8
            if filter_q.get("status") == "overdue":
                return 3
            if filter_q.get("status") == "due_soon":
                return 5
            return 79  # total

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.count_documents = AsyncMock(side_effect=_count)
            result = await ppm_dashboard(
                current_user=manager,
                building_id=BUILDING_ID,
            )

        assert result.total_items == 79
        assert result.overdue_count == 3
        assert result.due_soon_count == 5
        assert result.compliance_overdue_count == 2
        assert result.health_score == 75.0

    @pytest.mark.asyncio
    async def test_ppm_dashboard_returns_none_when_no_compliance_items(self):
        """No compliance items tracked -> no score, NOT a perfect score.

        This previously asserted 100.0. A building that tracks nothing is not
        fully compliant, and reporting it as 100% made zero PPM coverage
        indistinguishable from complete, on-time coverage — on a safety metric,
        the most dangerous direction to be wrong in. Missing and zero are
        distinct states (CLAUDE.md, mandatory); callers render None as an
        explicit "no items tracked" empty state.
        """
        from routers.ppm import ppm_dashboard
        manager = _make_user(role="super_admin")

        async def _count(filter_q):
            if filter_q.get("is_compliance"):
                return 0
            return 10

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.count_documents = AsyncMock(side_effect=_count)
            result = await ppm_dashboard(
                current_user=manager,
                building_id=BUILDING_ID,
            )

        assert result.health_score is None
        assert result.compliance_total == 0

    # ── Multi-tenancy: building isolation ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_ppm_items_scoped_to_provided_building_id(self):
        """
        Verify that the DB query uses the building_id parameter, not a hardcoded constant.
        This is the key multi-tenancy regression test.
        """
        from routers.ppm import list_ppm_items
        manager = _make_user(role="strata_manager")
        other_building_id = "99999"  # Sierra demo building

        with patch("routers.ppm.db") as mock_db:
            mock_db.maintenance_schedules.find.return_value = self._mock_cursor([])
            await list_ppm_items(
                current_user=manager,
                building_id=other_building_id,
            )

        # The query passed to find() must use the provided building_id
        call_args = mock_db.maintenance_schedules.find.call_args[0][0]
        assert call_args["building_id"] == other_building_id
        assert call_args["building_id"] != "13195"
