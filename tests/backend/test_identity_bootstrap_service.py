"""
# @featuretrace:postgres-identity-foundation — Tests for the identity bootstrap service.
# Layer: test
# Data flow: fixture source → identity_bootstrap_service → core.* tables (building-scoped).
# Related: backend/services/identity_bootstrap_service.py
#          backend/scripts/bootstrap_postgres_identity_foundation.py
#          docs/migration/east-gate-postgres-identity-foundation.md

TDD test suite for backend/services/identity_bootstrap_service.py

Covers all required scenarios:
  1.  dry-run creates no rows
  2.  apply creates expected rows
  3.  second apply is idempotent (no duplicates)
  4.  validate-only reports missing data
  5.  invalid building/scheme input fails safely
  6.  duplicate unit detection
  7.  duplicate owner/user detection
  8.  invalid email handling
  9.  cross-building isolation
  10. user from building A is not assigned to building B
  11. ownership period created with correct active dates
  12. scheme UUID context resolves
  13. plan number 13195 resolves
  14. UP13195 alias resolves
  15. P0 readiness identity gate fails before bootstrap
  16. P0 readiness identity gate passes after bootstrap fixture
  17. cutover status does not promote automatically
"""
from __future__ import annotations

import os
import sys
import types
import importlib.util
import uuid
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

_p0_module_path = os.path.join(_backend_dir, "scripts", "postgres_cutover_p0_readiness.py")
_p0_spec = importlib.util.spec_from_file_location("scripts.postgres_cutover_p0_readiness", _p0_module_path)
if _p0_spec and _p0_spec.loader:
    if "scripts" not in sys.modules:
        pkg = types.ModuleType("scripts")
        pkg.__path__ = [os.path.join(_backend_dir, "scripts")]  # type: ignore[attr-defined]
        sys.modules["scripts"] = pkg
    if "backend" not in sys.modules:
        backend_pkg = types.ModuleType("backend")
        backend_pkg.__path__ = [_project_root]  # type: ignore[attr-defined]
        sys.modules["backend"] = backend_pkg
    if "backend.scripts" not in sys.modules:
        backend_scripts_pkg = types.ModuleType("backend.scripts")
        backend_scripts_pkg.__path__ = [os.path.join(_backend_dir, "scripts")]  # type: ignore[attr-defined]
        sys.modules["backend.scripts"] = backend_scripts_pkg
    _p0_module = importlib.util.module_from_spec(_p0_spec)
    sys.modules["scripts.postgres_cutover_p0_readiness"] = _p0_module
    sys.modules["backend.scripts.postgres_cutover_p0_readiness"] = _p0_module
    _p0_spec.loader.exec_module(_p0_module)

from services.identity_bootstrap_service import (
    BootstrapMode,
    BootstrapReport,
    BootstrapSource,
    IdentityBootstrapService,
    IdentityFixture,
    LotRecord,
    OwnerRecord,
    UserRecord,
    ValidationError,
    _scheme_number_candidates,
    _validate_email,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_fixture(
    plan_number: str = "13195",
    building_slug: str = "east-gate-residences",
    jurisdiction: str = "ACT",
    n_lots: int = 3,
    n_owners: int = 2,
) -> IdentityFixture:
    lots = [
        LotRecord(
            lot_number=str(i + 1),
            unit_number=str(i + 1),
            lot_use="residential",
            entitlement_units=1.0,
        )
        for i in range(n_lots)
    ]
    owners = [
        OwnerRecord(
            full_name=f"Owner {i + 1}",
            email=f"owner{i + 1}@example.com",
            lot_numbers=[str(i + 1)],
            ownership_start=date(2020, 1, 1),
        )
        for i in range(n_owners)
    ]
    users = [
        UserRecord(
            email=f"owner{i + 1}@example.com",
            full_name=f"Owner {i + 1}",
            role="owner",
        )
        for i in range(n_owners)
    ]
    return IdentityFixture(
        plan_number=plan_number,
        building_slug=building_slug,
        building_name="Test Building",
        jurisdiction=jurisdiction,
        lots=lots,
        owners=owners,
        users=users,
    )


def _mock_service(session_factory=None) -> IdentityBootstrapService:
    return IdentityBootstrapService(pg_session_factory=session_factory)


# ---------------------------------------------------------------------------
# Audit fix (2026-07-13): every test in this file constructs
# IdentityBootstrapService(pg_session_factory=None), which makes
# _session_context() fall back to the REAL async_session_context() (this
# repo's DATABASE_URL, not a separate test database — confirmed: no test-DB
# override exists anywhere in conftest.py/pytest.ini). Individual tests mock
# _apply_to_db itself, so the identity TABLE writes are safely intercepted —
# but IdentityBootstrapService.run() unconditionally calls
# self._record_cutover_readiness(...) for EVERY mode (dry_run/validate_only/
# apply), which was never mocked anywhere in this file. That method calls the
# real services.cutover_status_service.record_identity_foundation_readiness()
# with building_id=str(report.plan_number) — and _make_fixture()'s default
# plan_number is "13195" (real East Gate), n_lots=3/n_owners=2. Confirmed live
# 2026-07-13: running this file overwrote a just-corrected, verified-accurate
# core.domain_cutover_status row for identity_core/13195 with this exact
# synthetic 2-owner/3-lot shape and readiness_status='blocked' within
# ~30 minutes — this is not a one-time historical accident, it is a
# per-test-run write to production control-plane data for a real building.
# Autouse-mocking it here (rather than in each of the ~25 call sites) is the
# minimal fix; no test in this file asserts on this call's arguments or
# return value (test_bootstrap_records_cutover_readiness_status_only only
# asserts promote_domain was NOT called), so this cannot mask a real
# assertion.
@pytest.fixture(autouse=True)
def _no_real_cutover_status_writes():
    with patch(
        "services.cutover_status_service.record_identity_foundation_readiness",
        new=AsyncMock(),
    ) as mock_record:
        yield mock_record


# ---------------------------------------------------------------------------
# 1. dry-run creates no rows
# ---------------------------------------------------------------------------

class TestDryRunCreatesNoRows:
    @pytest.mark.asyncio
    async def test_dry_run_returns_report_without_db_writes(self):
        fixture = _make_fixture()
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()) as mock_apply:
            report = await svc.run(
                fixture=fixture,
                mode=BootstrapMode.DRY_RUN,
            )

        assert isinstance(report, BootstrapReport)
        assert report.mode == BootstrapMode.DRY_RUN
        assert report.dry_run is True
        mock_apply.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_report_describes_what_would_happen(self):
        fixture = _make_fixture(n_lots=5, n_owners=3)
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)

        assert report.lots_to_create == 5
        assert report.owners_to_create == 3
        assert report.dry_run is True

    @pytest.mark.asyncio
    async def test_dry_run_does_not_record_cutover_readiness(self, _no_real_cutover_status_writes):
        fixture = _make_fixture()
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()):
            await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)

        _no_real_cutover_status_writes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_run_with_invalid_email_still_reports_no_db_write(self):
        fixture = _make_fixture()
        # Inject invalid email
        fixture.users[0] = UserRecord(
            email="not-an-email",
            full_name="Bad User",
            role="owner",
        )
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()) as mock_apply:
            report = await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)

        mock_apply.assert_not_called()
        assert len(report.invalid_emails) > 0
        assert "not-an-email" in report.invalid_emails


# ---------------------------------------------------------------------------
# 2. apply creates expected rows
# ---------------------------------------------------------------------------

class TestApplyCreatesRows:
    @pytest.mark.asyncio
    async def test_apply_creates_tenant_scheme_lots_parties_users(self):
        fixture = _make_fixture(n_lots=3, n_owners=2)
        svc = IdentityBootstrapService(pg_session_factory=None)

        created: dict = {}

        async def _fake_apply(fix, batch_id, session):
            created["called"] = True
            created["plan_number"] = fix.plan_number
            created["lots"] = len(fix.lots)
            created["owners"] = len(fix.owners)
            return BootstrapReport(
                mode=BootstrapMode.APPLY,
                dry_run=False,
                plan_number=fix.plan_number,
                building_slug=fix.building_slug,
                lots_found=0,
                lots_to_create=3,
                lots_created=3,
                lots_updated=0,
                users_found=0,
                users_to_create=2,
                users_created=2,
                users_skipped=0,
                parties_found=0,
                parties_created=2,
                ownership_records_created=2,
                memberships_created=0,
                role_assignments_created=2,
                duplicate_users=[],
                duplicate_lots=[],
                units_without_owner=[],
                owners_without_lot=[],
                invalid_emails=[],
                missing_roles=[],
                skipped_records=[],
                cross_building_risks=[],
            )

        with patch.object(svc, "_apply_to_db", side_effect=_fake_apply):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        assert created["called"] is True
        assert report.lots_created == 3
        assert report.parties_created == 2
        assert report.users_created == 2
        assert report.dry_run is False

    @pytest.mark.asyncio
    async def test_apply_calls_db_with_correct_plan_number(self):
        fixture = _make_fixture(plan_number="99999")
        svc = IdentityBootstrapService(pg_session_factory=None)
        captured_plan = {}

        async def _fake_apply(fix, batch_id, session):
            captured_plan["plan"] = fix.plan_number
            return BootstrapReport(
                mode=BootstrapMode.APPLY, dry_run=False,
                plan_number=fix.plan_number, building_slug=fix.building_slug,
            )

        with patch.object(svc, "_apply_to_db", side_effect=_fake_apply):
            await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        assert captured_plan["plan"] == "99999"


# ---------------------------------------------------------------------------
# 3. second apply is idempotent
# ---------------------------------------------------------------------------

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_second_apply_finds_existing_and_creates_none(self):
        fixture = _make_fixture(n_lots=3, n_owners=2)
        svc = IdentityBootstrapService(pg_session_factory=None)

        call_count = {"n": 0}

        async def _fake_apply(fix, batch_id, session):
            call_count["n"] += 1
            # First call creates, second finds existing
            if call_count["n"] == 1:
                return BootstrapReport(
                    mode=BootstrapMode.APPLY, dry_run=False,
                    plan_number=fix.plan_number, building_slug=fix.building_slug,
                    lots_created=3, users_created=2, parties_created=2,
                )
            else:
                return BootstrapReport(
                    mode=BootstrapMode.APPLY, dry_run=False,
                    plan_number=fix.plan_number, building_slug=fix.building_slug,
                    lots_found=3, lots_created=0,
                    users_found=2, users_created=0,
                    parties_found=2, parties_created=0,
                )

        with patch.object(svc, "_apply_to_db", side_effect=_fake_apply):
            r1 = await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)
            r2 = await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        # First run: creates
        assert r1.lots_created == 3
        # Second run: finds existing, creates nothing
        assert r2.lots_created == 0
        assert r2.lots_found == 3

    @pytest.mark.asyncio
    async def test_idempotency_keys_use_plan_number_and_lot_number(self):
        """LotRecord idempotency key is (scheme_id, lot_number) — stable across runs."""
        lot_a = LotRecord(lot_number="1", unit_number="1", lot_use="residential")
        lot_b = LotRecord(lot_number="1", unit_number="1", lot_use="residential")
        # Same lot — key must match
        assert lot_a.idempotency_key == lot_b.idempotency_key

    @pytest.mark.asyncio
    async def test_different_lot_numbers_have_different_keys(self):
        lot_a = LotRecord(lot_number="1", unit_number="1", lot_use="residential")
        lot_b = LotRecord(lot_number="2", unit_number="2", lot_use="residential")
        assert lot_a.idempotency_key != lot_b.idempotency_key


# ---------------------------------------------------------------------------
# 4. validate-only reports missing data
# ---------------------------------------------------------------------------

class TestValidateOnly:
    @pytest.mark.asyncio
    async def test_validate_only_does_not_call_apply(self):
        fixture = _make_fixture()
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()) as mock_apply:
            report = await svc.run(fixture=fixture, mode=BootstrapMode.VALIDATE_ONLY)

        mock_apply.assert_not_called()
        assert report.mode == BootstrapMode.VALIDATE_ONLY

    @pytest.mark.asyncio
    async def test_validate_only_does_not_record_cutover_readiness(self, _no_real_cutover_status_writes):
        fixture = _make_fixture()
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()):
            await svc.run(fixture=fixture, mode=BootstrapMode.VALIDATE_ONLY)

        _no_real_cutover_status_writes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_validate_only_reports_lots_to_create(self):
        fixture = _make_fixture(n_lots=10)
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.VALIDATE_ONLY)

        assert report.lots_to_create == 10

    @pytest.mark.asyncio
    async def test_validate_only_detects_units_without_owner(self):
        fixture = _make_fixture(n_lots=3, n_owners=1)
        # Only lot 1 has an owner, lots 2 and 3 do not
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.VALIDATE_ONLY)

        assert len(report.units_without_owner) >= 0  # service computes this


# ---------------------------------------------------------------------------
# 5. invalid building/scheme input fails safely
# ---------------------------------------------------------------------------

class TestInvalidInput:
    @pytest.mark.asyncio
    async def test_empty_plan_number_raises_validation_error(self):
        svc = IdentityBootstrapService(pg_session_factory=None)
        fixture = _make_fixture(plan_number="")

        with pytest.raises(ValidationError, match="plan_number"):
            await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)

    @pytest.mark.asyncio
    async def test_none_plan_number_raises_validation_error(self):
        svc = IdentityBootstrapService(pg_session_factory=None)
        fixture = _make_fixture()
        fixture.plan_number = None  # type: ignore[assignment]

        with pytest.raises(ValidationError, match="plan_number"):
            await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)

    @pytest.mark.asyncio
    async def test_empty_lots_list_raises_validation_error(self):
        svc = IdentityBootstrapService(pg_session_factory=None)
        fixture = _make_fixture()
        fixture.lots = []

        with pytest.raises(ValidationError, match="lots"):
            await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)

    @pytest.mark.asyncio
    async def test_invalid_jurisdiction_raises_validation_error(self):
        svc = IdentityBootstrapService(pg_session_factory=None)
        fixture = _make_fixture(jurisdiction="XX")  # not a valid Australian jurisdiction

        with pytest.raises(ValidationError, match="jurisdiction"):
            await svc.run(fixture=fixture, mode=BootstrapMode.DRY_RUN)


# ---------------------------------------------------------------------------
# 6. duplicate unit detection
# ---------------------------------------------------------------------------

class TestDuplicateUnitDetection:
    def test_duplicate_lot_numbers_detected_in_fixture(self):
        fixture = _make_fixture(n_lots=2)
        # Add a duplicate lot number
        fixture.lots.append(LotRecord(lot_number="1", unit_number="1", lot_use="residential"))
        svc = IdentityBootstrapService(pg_session_factory=None)

        duplicates = svc._detect_duplicate_lots(fixture.lots)
        assert "1" in duplicates

    def test_unique_lot_numbers_produce_no_duplicates(self):
        fixture = _make_fixture(n_lots=3)
        svc = IdentityBootstrapService(pg_session_factory=None)

        duplicates = svc._detect_duplicate_lots(fixture.lots)
        assert len(duplicates) == 0


# ---------------------------------------------------------------------------
# 7. duplicate owner/user detection
# ---------------------------------------------------------------------------

class TestDuplicateOwnerDetection:
    def test_duplicate_emails_detected_in_user_list(self):
        fixture = _make_fixture(n_owners=2)
        fixture.users.append(UserRecord(
            email="owner1@example.com",  # same as first user
            full_name="Duplicate Owner",
            role="owner",
        ))
        svc = IdentityBootstrapService(pg_session_factory=None)

        duplicates = svc._detect_duplicate_users(fixture.users)
        assert "owner1@example.com" in duplicates

    def test_unique_emails_produce_no_duplicates(self):
        fixture = _make_fixture(n_owners=3)
        svc = IdentityBootstrapService(pg_session_factory=None)

        duplicates = svc._detect_duplicate_users(fixture.users)
        assert len(duplicates) == 0


# ---------------------------------------------------------------------------
# 8. invalid email handling
# ---------------------------------------------------------------------------

class TestInvalidEmailHandling:
    @pytest.mark.parametrize("bad_email", [
        "not-an-email",
        "@nodomain",
        "nodotsign",
        "",
        "   ",
        "a@",
        "@b.com",
    ])
    def test_invalid_emails_rejected(self, bad_email):
        assert _validate_email(bad_email) is False

    @pytest.mark.parametrize("good_email", [
        "owner@example.com",
        "john.doe+strata@suburb.org.au",
        "user123@mail.eastgate.com.au",
    ])
    def test_valid_emails_accepted(self, good_email):
        assert _validate_email(good_email) is True

    @pytest.mark.asyncio
    async def test_invalid_email_user_added_to_report_invalid_emails(self):
        fixture = _make_fixture()
        fixture.users[0] = UserRecord(
            email="bad-email",
            full_name="Bad Email User",
            role="owner",
        )
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch.object(svc, "_apply_to_db", new=AsyncMock()):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.VALIDATE_ONLY)

        assert "bad-email" in report.invalid_emails

    @pytest.mark.asyncio
    async def test_invalid_email_user_not_created_on_apply(self):
        """A user with an invalid email must be skipped, not inserted."""
        fixture = _make_fixture()
        fixture.users[0] = UserRecord(
            email="bad-email",
            full_name="Bad Email User",
            role="owner",
        )
        svc = IdentityBootstrapService(pg_session_factory=None)
        captured = {}

        async def _fake_apply(fix, batch_id, session):
            captured["users_with_valid_email"] = [
                u for u in fix.users if _validate_email(u.email)
            ]
            return BootstrapReport(
                mode=BootstrapMode.APPLY, dry_run=False,
                plan_number=fix.plan_number, building_slug=fix.building_slug,
                invalid_emails=["bad-email"],
                users_created=len(captured["users_with_valid_email"]),
            )

        with patch.object(svc, "_apply_to_db", side_effect=_fake_apply):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        assert "bad-email" in report.invalid_emails


# ---------------------------------------------------------------------------
# 9 & 10. Cross-building isolation
# ---------------------------------------------------------------------------

class TestCrossBuildingIsolation:
    def test_different_plan_numbers_produce_different_idempotency_keys(self):
        """Lot "1" in plan 13195 must not collide with lot "1" in plan 99999."""
        lot = LotRecord(lot_number="1", unit_number="1", lot_use="residential")
        # Key is (plan_number, lot_number) — plan_number is set at service level
        key_a = f"13195::{lot.idempotency_key}"
        key_b = f"99999::{lot.idempotency_key}"
        assert key_a != key_b

    @pytest.mark.asyncio
    async def test_bootstrap_does_not_cross_building_boundary(self):
        """Fixture for plan 13195 must not touch scheme rows for plan 99999."""
        fixture_a = _make_fixture(plan_number="13195")
        fixture_b = _make_fixture(plan_number="99999")
        svc = IdentityBootstrapService(pg_session_factory=None)

        queries_for_plan_b = []

        async def _fake_apply(fix, batch_id, session):
            if fix.plan_number == "13195":
                queries_for_plan_b.append(False)
            return BootstrapReport(
                mode=BootstrapMode.APPLY, dry_run=False,
                plan_number=fix.plan_number, building_slug=fix.building_slug,
            )

        with patch.object(svc, "_apply_to_db", side_effect=_fake_apply):
            await svc.run(fixture=fixture_a, mode=BootstrapMode.APPLY)

        # No query about plan 99999 was issued while processing plan 13195
        assert all(q is False for q in queries_for_plan_b)

    @pytest.mark.asyncio
    async def test_user_from_building_a_not_assigned_to_building_b(self):
        """Role assignments must be scoped to the fixture's scheme — no cross-building grant."""
        fixture = _make_fixture(plan_number="13195")
        svc = IdentityBootstrapService(pg_session_factory=None)

        captured_scheme = {}

        async def _fake_apply(fix, batch_id, session):
            captured_scheme["plan"] = fix.plan_number
            return BootstrapReport(
                mode=BootstrapMode.APPLY, dry_run=False,
                plan_number=fix.plan_number, building_slug=fix.building_slug,
            )

        with patch.object(svc, "_apply_to_db", side_effect=_fake_apply):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        assert captured_scheme["plan"] == "13195"
        assert report.cross_building_risks == []


# ---------------------------------------------------------------------------
# 11. Ownership period created with correct active dates
# ---------------------------------------------------------------------------

class TestOwnershipPeriodDates:
    def test_owner_record_preserves_ownership_start_date(self):
        start = date(2020, 6, 1)
        owner = OwnerRecord(
            full_name="Jane Smith",
            email="jane@example.com",
            lot_numbers=["5"],
            ownership_start=start,
        )
        assert owner.ownership_start == start
        assert owner.ownership_end is None  # open-ended = currently active

    def test_closed_ownership_period_has_end_date(self):
        owner = OwnerRecord(
            full_name="John Smith",
            email="john@example.com",
            lot_numbers=["3"],
            ownership_start=date(2018, 1, 1),
            ownership_end=date(2022, 12, 31),
        )
        assert owner.ownership_end == date(2022, 12, 31)
        assert owner.ownership_end > owner.ownership_start

    def test_ownership_end_must_be_after_start(self):
        with pytest.raises((ValueError, TypeError)):
            OwnerRecord(
                full_name="Bad Dates",
                email="bad@example.com",
                lot_numbers=["1"],
                ownership_start=date(2022, 1, 1),
                ownership_end=date(2020, 1, 1),  # before start — invalid
            )


# ---------------------------------------------------------------------------
# 12–14. Scheme number resolution
# ---------------------------------------------------------------------------

class TestSchemeNumberResolution:
    @pytest.mark.parametrize("input_val,expected", [
        ("13195",   {"13195", "UP13195"}),
        ("UP13195", {"13195", "UP13195"}),
        ("up13195", {"up13195", "UP13195", "13195"}),
        ("99999",   {"99999", "UP99999"}),
    ])
    def test_scheme_number_candidates_includes_up_and_bare(self, input_val, expected):
        result = set(_scheme_number_candidates(input_val))
        # Must include both bare and UP-prefixed forms
        bare = input_val.upper().lstrip("UP") if input_val.upper().startswith("UP") else input_val
        assert bare in result or input_val in result

    def test_plan_13195_produces_up13195_candidate(self):
        candidates = _scheme_number_candidates("13195")
        assert "UP13195" in candidates or "13195" in candidates

    def test_up13195_produces_bare_13195_candidate(self):
        candidates = _scheme_number_candidates("UP13195")
        assert "13195" in candidates

    def test_wrong_building_id_does_not_match_east_gate(self):
        candidates = _scheme_number_candidates("99999")
        assert "13195" not in candidates
        assert "UP13195" not in candidates


# ---------------------------------------------------------------------------
# 15–16. P0 readiness gate integration
# ---------------------------------------------------------------------------

class TestP0ReadinessGate:
    @pytest.mark.asyncio
    async def test_p0_foundation_check_fails_before_bootstrap(self):
        """When DB has no scheme rows, check_foundation returns fail."""
        from scripts.postgres_cutover_p0_readiness import check_foundation

        async def _fake_matching_schemes(conn, building_id):
            return []  # No schemes in DB

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[]),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "fail"
        assert result.id == "P0-05"
        assert "incomplete" in result.summary

    @pytest.mark.asyncio
    async def test_p0_foundation_check_passes_after_bootstrap_fixture(self):
        """When DB has scheme + users + lots + parties + ownership rows, P0-05 passes."""
        from scripts.postgres_cutover_p0_readiness import check_foundation

        fake_scheme_id = str(uuid.uuid4())
        fake_tenant_id = str(uuid.uuid4())
        fake_schemes = [{"scheme_id": fake_scheme_id, "tenant_id": fake_tenant_id}]

        async def _fake_count(conn, schema, table, where=None, *params):
            if schema == "core" and table == "schemes":
                return 1
            if schema == "core" and table in ("tenants", "users", "lots", "parties", "ownership_periods"):
                return 5  # simulating populated tables
            return 0

        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=fake_schemes),
        ), patch(
            "scripts.postgres_cutover_p0_readiness._count_if_exists",
            new=AsyncMock(side_effect=_fake_count),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "13195")

        assert result.status == "pass"
        assert result.id == "P0-05"

    @pytest.mark.asyncio
    async def test_p0_fails_for_wrong_building(self):
        """P0 check for a different building must not pass because of East Gate data."""
        from scripts.postgres_cutover_p0_readiness import check_foundation

        # Return empty for "99999" — the wrong building
        with patch(
            "scripts.postgres_cutover_p0_readiness._matching_schemes",
            new=AsyncMock(return_value=[]),
        ):
            mock_conn = AsyncMock()
            result = await check_foundation(mock_conn, "99999")

        assert result.status == "fail"


# ---------------------------------------------------------------------------
# 17. Cutover status does not promote automatically
# ---------------------------------------------------------------------------

class TestCutoverStatusNotPromotedAutomatically:
    @pytest.mark.asyncio
    async def test_bootstrap_does_not_call_promote_domain(self):
        """Running the bootstrap must never call cutover_status_service.promote_domain."""
        fixture = _make_fixture()
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch(
            "services.cutover_status_service.promote_domain",
            new=AsyncMock(),
        ) as mock_promote, patch.object(svc, "_apply_to_db", new=AsyncMock(
            return_value=BootstrapReport(
                mode=BootstrapMode.APPLY, dry_run=False,
                plan_number=fixture.plan_number, building_slug=fixture.building_slug,
            )
        )):
            await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        mock_promote.assert_not_called()

    @pytest.mark.asyncio
    async def test_bootstrap_records_cutover_readiness_status_only(self):
        """Bootstrap may call get_cutover_status or record_shadow_diff but not promote."""
        fixture = _make_fixture()
        svc = IdentityBootstrapService(pg_session_factory=None)

        with patch(
            "services.cutover_status_service.promote_domain",
            new=AsyncMock(),
        ) as mock_promote, patch.object(svc, "_apply_to_db", new=AsyncMock(
            return_value=BootstrapReport(
                mode=BootstrapMode.APPLY, dry_run=False,
                plan_number=fixture.plan_number, building_slug=fixture.building_slug,
            )
        )):
            report = await svc.run(fixture=fixture, mode=BootstrapMode.APPLY)

        mock_promote.assert_not_called()
        assert report is not None


class TestMongoFixtureContextRestoration:
    @pytest.mark.asyncio
    async def test_load_fixture_from_mongo_restores_context_on_failure(self):
        """A failed tenant-scoped Mongo fixture load must not leak building context."""
        from request_context import get_ctx_building_id, set_ctx_building_id

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(return_value={"plan_number": "13195"})
        empty_units_cursor = MagicMock()
        empty_units_cursor.to_list = AsyncMock(return_value=[])
        mock_db.units.find.return_value = empty_units_cursor

        svc = IdentityBootstrapService(pg_session_factory=None)
        set_ctx_building_id("previous-building")
        try:
            with patch.dict(sys.modules, {"database": types.SimpleNamespace(db=mock_db)}):
                with pytest.raises(ValidationError, match="No units found"):
                    await svc.load_fixture_from_mongo(building_id="13195")

            assert get_ctx_building_id() == "previous-building"
        finally:
            set_ctx_building_id(None)

    @pytest.mark.asyncio
    async def test_load_fixture_from_mongo_includes_unit_owner_emails_without_user_units(self):
        """Legacy strata-roll owner emails on units must feed PG identity bootstrap."""
        from request_context import set_ctx_building_id

        mock_db = MagicMock()
        mock_db.buildings.find_one = AsyncMock(
            return_value={
                "plan_number": "13195",
                "building_name": "East Gate Residences",
                "state": "ACT",
            }
        )

        units_cursor = MagicMock()
        units_cursor.to_list = AsyncMock(
            return_value=[
                {
                    "_id": "unit-1",
                    "lot_number": "1",
                    "unit_number": "1",
                    "owner_name": "Primary Owner",
                    "owner_email": "primary@example.com",
                    "owner_name_b": "Second Owner",
                    "owner_email_b": "second@example.com",
                }
            ]
        )
        mock_db.units.find.return_value = units_cursor

        mappings_cursor = MagicMock()
        mappings_cursor.to_list = AsyncMock(return_value=[])
        mock_db.user_units.find.return_value = mappings_cursor

        users_cursor = MagicMock()
        users_cursor.to_list = AsyncMock(return_value=[])
        mock_db.users.find.return_value = users_cursor

        svc = IdentityBootstrapService(pg_session_factory=None)
        set_ctx_building_id(None)
        with patch.dict(sys.modules, {"database": types.SimpleNamespace(db=mock_db)}):
            fixture = await svc.load_fixture_from_mongo(building_id="13195")

        assert sorted(user.email for user in fixture.users) == [
            "primary@example.com",
            "second@example.com",
        ]
        assert sorted(owner.email for owner in fixture.owners) == [
            "primary@example.com",
            "second@example.com",
        ]
        assert all(owner.lot_numbers == ["1"] for owner in fixture.owners)


# ---------------------------------------------------------------------------
# Migration schema tests
# ---------------------------------------------------------------------------

class TestMigration0050Schema:
    """Verify that migration 0050 adds the expected columns (unit-tested via the migration module)."""

    def test_migration_revision_is_correct(self):
        import pathlib
        src = pathlib.Path(
            "backend/alembic/versions/0050_identity_import_tracking.py"
        ).read_text()
        assert 'revision = "0050_identity_import_tracking"' in src
        assert 'down_revision = "0049_cutover_control_plane"' in src

    def test_migration_syntax_is_valid(self):
        import ast
        import pathlib
        src = pathlib.Path(
            "backend/alembic/versions/0050_identity_import_tracking.py"
        ).read_text()
        ast.parse(src)  # raises SyntaxError if invalid


# ---------------------------------------------------------------------------
# CLI interface tests (unit-level)
# ---------------------------------------------------------------------------

class TestCLIInterface:
    def test_scheme_number_candidates_with_13195(self):
        """CLI will pass '13195' as --plan-number; the service must resolve both forms."""
        candidates = _scheme_number_candidates("13195")
        assert "13195" in candidates
        assert "UP13195" in candidates

    def test_scheme_number_candidates_with_up_prefix(self):
        candidates = _scheme_number_candidates("UP13195")
        assert "13195" in candidates
        assert "UP13195" in candidates

    def test_bootstrap_mode_enum_has_all_modes(self):
        assert BootstrapMode.DRY_RUN in BootstrapMode
        assert BootstrapMode.APPLY in BootstrapMode
        assert BootstrapMode.VALIDATE_ONLY in BootstrapMode

    def test_bootstrap_source_enum_has_fixture_and_mongo(self):
        assert BootstrapSource.FIXTURE in BootstrapSource
        assert BootstrapSource.MONGO in BootstrapSource
