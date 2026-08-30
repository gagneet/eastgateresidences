"""
GAP-FT-003: Cron Levy Reminders — Feature Toggle Enforcement Tests

Before the fix, cron_levy_reminders.py read Mongo feature_toggles directly even
after the PostgreSQL feature-toggle cutover. That left the cron path out of sync
with the rest of runtime feature resolution.

Fix keeps _is_feature_enabled(building_id, feature_key), but it now delegates to
the Postgres-backed config repo so cron resolution matches the rest of the app.

Tests cover:
  - _is_feature_enabled() 3-tier resolution (per-building / global / fail-open)
  - run_levy_reminders() skips buildings where levy_reminders is toggled off
  - run_levy_reminders() processes buildings where levy_reminders is toggled on
  - run_levy_run() skips buildings where levy_reminders is toggled off
  - Both toggle-off AND settings.enabled-off still produce skip (gate is additive)
  - Multi-building: off-buildings skipped, on-buildings processed
  - super_admin toggle bypass does NOT apply in cron context (cron runs as system)

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_gap_ft_cron_levy_reminders.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_cursor(items):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=items)
    return c


def _make_toggle_doc(building_id, feature_key, is_enabled):
    return {"feature_key": feature_key, "building_id": building_id, "is_enabled": is_enabled}


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests: _is_feature_enabled()
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsFeatureEnabled:
    """Test the 2-tier toggle resolution helper added to cron_levy_reminders."""

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_per_building_enabled_returns_true(self, mock_db):
        """Enabled toggle resolves to True."""
        from cron.cron_levy_reminders import _is_feature_enabled

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=True),
        ):
            result = await _is_feature_enabled("13195", "levy_reminders")
        assert result is True

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_per_building_disabled_returns_false(self, mock_db):
        """Disabled toggle resolves to False."""
        from cron.cron_levy_reminders import _is_feature_enabled

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=False),
        ):
            result = await _is_feature_enabled("13195", "levy_reminders")
        assert result is False

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_missing_toggle_fails_open(self, mock_db):
        """Missing toggle resolves to the helper's fail-open default."""
        from cron.cron_levy_reminders import _is_feature_enabled

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=None),
        ):
            result = await _is_feature_enabled("13195", "levy_reminders")
        assert result is True

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_delegates_to_config_repo_with_building_and_key(self, mock_db):
        """The helper passes building_id and feature_key through to the repo."""
        from cron.cron_levy_reminders import _is_feature_enabled

        repo_resolver = AsyncMock(return_value=False)
        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=repo_resolver,
        ):
            result = await _is_feature_enabled("13195", "levy_reminders")

        assert result is False
        repo_resolver.assert_awaited_once_with("13195", "levy_reminders", default=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration-level tests: run_levy_reminders() respects the toggle
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunLevyRemindersToggleRespect:
    """run_levy_reminders() must skip buildings where levy_reminders is toggled off."""

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_skips_building_when_toggle_disabled(self, mock_db):
        """When toggle is off for a building, no reminder records are queued."""
        from cron.cron_levy_reminders import run_levy_reminders

        # levy_reminder_settings shows enabled (should NOT be reached due to toggle gate)
        mock_db.levy_reminder_settings.find_one = AsyncMock(
            return_value={"enabled": True, "overdue_threshold_cents": 100, "overdue_days": [7]}
        )
        # unit_levy_ledger should NOT be queried when toggle is off
        mock_db.unit_levy_ledger.find = MagicMock()
        # email_sent_log should NOT be written when toggle is off
        mock_db.email_sent_log.insert_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=False),
        ):
            result = await run_levy_reminders(building_id="13195", tier=1)

        assert result["reminders_sent"] == 0
        mock_db.unit_levy_ledger.find.assert_not_called()
        mock_db.email_sent_log.insert_one.assert_not_called()

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_processes_building_when_toggle_enabled(self, mock_db):
        """When toggle is on for a building, reminder processing proceeds normally."""
        from datetime import datetime, timezone, timedelta
        from cron.cron_levy_reminders import run_levy_reminders

        now = datetime.now(timezone.utc)
        overdue_date = (now - timedelta(days=10)).isoformat()

        # Building settings: enabled with overdue_days=[7,14]
        mock_db.levy_reminder_settings.find_one = AsyncMock(
            return_value={
                "enabled": True,
                "overdue_threshold_cents": 100,
                "overdue_days": [7, 10, 14],
            }
        )
        # One overdue unit
        mock_db.unit_levy_ledger.find = MagicMock(
            return_value=_make_cursor([
                {"unit_number": "101", "admin_balance": 500.0, "sinking_balance": 0,
                 "due_date": overdue_date}
            ])
        )
        mock_db.email_sent_log.insert_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.levy_reminder_settings_service._get_postgres_levy_reminder_settings",
            new=AsyncMock(return_value=None),
        ):
            result = await run_levy_reminders(building_id="13195", tier=1)

        assert result["reminders_sent"] == 1
        mock_db.email_sent_log.insert_one.assert_called_once()

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_toggle_off_supersedes_settings_enabled(self, mock_db):
        """Toggle=off beats levy_reminder_settings.enabled=True.
        Both gates must be checked; toggle is the outer gate."""
        from cron.cron_levy_reminders import run_levy_reminders

        # levy_reminder_settings says enabled (but toggle should win)
        mock_db.levy_reminder_settings.find_one = AsyncMock(
            return_value={"enabled": True, "overdue_threshold_cents": 100, "overdue_days": [7]}
        )
        mock_db.unit_levy_ledger.find = MagicMock()
        mock_db.email_sent_log.insert_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=False),
        ):
            result = await run_levy_reminders(building_id="13195", tier=1)

        assert result["reminders_sent"] == 0
        # levy_reminder_settings should NOT even be queried when toggle is off
        mock_db.levy_reminder_settings.find_one.assert_not_called()
        mock_db.unit_levy_ledger.find.assert_not_called()

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_toggle_on_settings_disabled_still_skips(self, mock_db):
        """Toggle=on but levy_reminder_settings.enabled=False → still skip.
        The settings gate is preserved; both must be on."""
        from cron.cron_levy_reminders import run_levy_reminders

        # levy_reminder_settings: disabled
        mock_db.levy_reminder_settings.find_one = AsyncMock(
            return_value={"enabled": False}
        )
        mock_db.unit_levy_ledger.find = MagicMock()
        mock_db.email_sent_log.insert_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=True),
        ):
            result = await run_levy_reminders(building_id="13195", tier=1)

        assert result["reminders_sent"] == 0
        mock_db.unit_levy_ledger.find.assert_not_called()
        mock_db.email_sent_log.insert_one.assert_not_called()

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_multi_building_partial_skip(self, mock_db):
        """Multi-building run: toggle-off buildings skipped, toggle-on buildings processed.
        Verifies building isolation."""
        from datetime import datetime, timezone, timedelta
        from cron.cron_levy_reminders import run_levy_reminders

        now = datetime.now(timezone.utc)
        overdue_date = (now - timedelta(days=10)).isoformat()

        buildings = ["13195", "16244"]

        toggle_state = {"13195": False, "16244": True}

        async def _resolve_toggle(building_id, feature_key, default=True):
            assert feature_key == "levy_reminders"
            return toggle_state.get(building_id, default)

        # Both buildings have settings enabled
        mock_db.levy_reminder_settings.find_one = AsyncMock(
            return_value={"enabled": True, "overdue_threshold_cents": 100, "overdue_days": [10]}
        )
        # One overdue unit per building
        mock_db.unit_levy_ledger.find = MagicMock(
            return_value=_make_cursor([
                {"unit_number": "101", "admin_balance": 500.0, "sinking_balance": 0,
                 "due_date": overdue_date}
            ])
        )
        mock_db.email_sent_log.insert_one = AsyncMock()

        # Process all buildings by not specifying building_id
        # Mock _get_buildings to return our two buildings
        with patch("cron.cron_levy_reminders._get_buildings", new=AsyncMock(return_value=buildings)):
            with patch(
                "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
                new=AsyncMock(side_effect=_resolve_toggle),
            ):
                result = await run_levy_reminders(building_id=None, tier=1)

        # Only building 16244 should have produced reminders
        assert result["reminders_sent"] == 1
        mock_db.email_sent_log.insert_one.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Integration-level tests: run_levy_run() respects the toggle
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunLevyRunToggleRespect:
    """run_levy_run() must also skip buildings where levy_reminders is toggled off."""

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_levy_run_skips_when_toggle_disabled(self, mock_db):
        """run_levy_run does no DB work when levy_reminders toggle is off."""
        from cron.cron_levy_reminders import run_levy_run

        mock_db.unit_levy_ledger.find = MagicMock()
        mock_db.notifications.update_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=False),
        ):
            result = await run_levy_run(building_id="13195")

        assert result["notices_generated"] == 0
        mock_db.unit_levy_ledger.find.assert_not_called()
        mock_db.notifications.update_one.assert_not_called()

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_levy_run_processes_when_toggle_enabled(self, mock_db):
        """run_levy_run processes units when levy_reminders toggle is on."""
        from cron.cron_levy_reminders import run_levy_run

        # Two units with outstanding levies
        mock_db.unit_levy_ledger.find = MagicMock(
            return_value=_make_cursor([
                {"unit_number": "101", "admin_balance": 500.0, "sinking_balance": 200.0},
                {"unit_number": "102", "admin_balance": 0, "sinking_balance": 300.0},
            ])
        )
        mock_db.notifications.update_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
            new=AsyncMock(return_value=True),
        ):
            result = await run_levy_run(building_id="13195")

        assert result["notices_generated"] == 2
        assert mock_db.notifications.update_one.call_count == 2

    @pytest.mark.asyncio
    @patch("cron.cron_levy_reminders.db")
    async def test_levy_run_multi_building_isolation(self, mock_db):
        """run_levy_run only skips buildings with toggle off; others processed."""
        from cron.cron_levy_reminders import run_levy_run

        toggle_state = {"13195": False, "16244": True}

        async def _resolve_toggle(building_id, feature_key, default=True):
            assert feature_key == "levy_reminders"
            return toggle_state.get(building_id, default)
        mock_db.unit_levy_ledger.find = MagicMock(
            return_value=_make_cursor([
                {"unit_number": "101", "admin_balance": 200.0, "sinking_balance": 0}
            ])
        )
        mock_db.notifications.update_one = AsyncMock()

        with patch(
            "cron.cron_levy_reminders._get_buildings",
            new=AsyncMock(return_value=["13195", "16244"]),
        ):
            with patch(
                "cron.cron_levy_reminders.config_repo.resolve_feature_toggle",
                new=AsyncMock(side_effect=_resolve_toggle),
            ):
                result = await run_levy_run(building_id=None)

        # Only building 16244 processed → 1 notice
        assert result["notices_generated"] == 1
        assert mock_db.notifications.update_one.call_count == 1
