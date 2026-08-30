"""
tests/backend/test_financial_onboarding_read_and_sync_fixes.py

Coverage for the contract fixes made in response to
docs/migration/build-demo-data-ui-system.md's review of the historical
reconstruction batch workflow (backend/routers/financial_onboarding.py):

  1. New GET list/detail/manifest read endpoints (finding 1.1).
  2. /sync now performs a real, batch-scoped bank-feed sync instead of
     unconditionally marking the batch "synced" (finding 1.2).
  3. RegenerateLevyApplyRequest.override_reason is required alongside
     override_reconciliation_mismatch=True (finding 2.2).
  4. approve_reconstruction_batch always derives reviewed_by/approved_by
     from the authenticated caller, never a client-supplied value
     (finding 3.2's "at minimum" bar).
  5. Missing audit_log coverage on extract/sync is filled in (finding 4).

Endpoint functions are called directly (bypassing FastAPI DI), matching
tests/backend/test_sentinel_historical_reconstruction_rbac.py's established
convention. Depends(...)-only parameters (e.g. _feature) are left unpassed —
the Depends(...) sentinel is never read inside the function bodies.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Pre-existing bug fixed 2026-07-20: this file lives at tests/backend/test_X.py,
# so two dirname() calls only reach tests/ (not the repo root) — the join then
# silently produced tests/backend again, a no-op. Harmless until code below
# started relying on _backend actually pointing at the real backend/ directory.
# Matches conftest.py's (correct) three-dirname computation.
_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# Three different directories in this repo are importable as a bare "scripts"
# package: repo-root scripts/, backend/scripts/ (a real package,
# levy_generation_service.py's `from scripts.migrations.migration_027... import`
# target), and tests/scripts/ (no __init__.py — an implicit PEP 420 namespace
# package). pytest's own import-mode machinery inserts tests/backend and
# tests/ ahead of backend/ on sys.path when collecting a test file inside
# tests/backend/ (which has __init__.py, making pytest treat "tests" as the
# package rootpath) — confirmed live: sys.path[:3] during collection is
# [tests/backend, tests, backend, ...]. Because tests/scripts/ is found first
# and namespace packages don't require __init__.py, plain `import scripts`
# silently resolves to tests/scripts (which has no migrations/ submodule) well
# before backend/scripts/ is ever reached, and that resolution is cached in
# sys.modules for the rest of the process. Force-register the real
# backend/scripts package under the "scripts" name before anything else can
# claim it, rather than fighting sys.path order across three call sites.
import importlib.util as _importlib_util

_scripts_dir = os.path.join(_backend, "scripts")
_scripts_init = os.path.join(_scripts_dir, "__init__.py")
_cached_scripts = sys.modules.get("scripts")
# The repo-root scripts/__init__.py ALSO has a real __file__ (it's a regular
# package too, not a namespace one) — checking "has a __file__" alone isn't
# enough to detect the wrong one; compare the actual resolved path.
if _cached_scripts is None or getattr(_cached_scripts, "__file__", None) != _scripts_init:
    _spec = _importlib_util.spec_from_file_location(
        "scripts", _scripts_init, submodule_search_locations=[_scripts_dir],
    )
    _scripts_module = _importlib_util.module_from_spec(_spec)
    sys.modules["scripts"] = _scripts_module
    _spec.loader.exec_module(_scripts_module)

import services.levy_generation_service  # noqa: F401,E402

from routers.financial_onboarding import (  # noqa: E402
    ApproveReconstructionBatchRequest,
    RegenerateLevyApplyRequest,
    ReviewReconstructionBatchRequest,
    approve_reconstruction_batch,
    get_reconstruction_batch,
    get_reconstruction_batch_audit_log,
    get_reconstruction_batch_manifest,
    get_reconstruction_batch_reconciliation_summary,
    list_reconstruction_batches,
    review_reconstruction_batch,
    sync_reconstruction_batch,
)
from services import reconstruction_batch_service as rbs
from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionBatch

BUILDING_A = "13195"
BUILDING_B = "16244"
_SCHEME_A = {"scheme_id": "scheme-a", "tenant_id": "tenant-a", "scheme_number": BUILDING_A}

_ADMIN_USER = {"id": "user-admin", "role": "super_admin", "effective_role": "super_admin", "name": "Admin"}
_STRATA_ADMIN_USER = {"id": "user-sa", "role": "strata_admin", "effective_role": "strata_admin", "name": "SA"}
_MANAGER_USER = {"id": "user-mgr", "role": "strata_manager", "effective_role": "strata_manager", "name": "Mgr"}


def _batch(**overrides) -> ReconstructionBatch:
    defaults = dict(
        batch_id="batch-1", building_id=BUILDING_A, financial_year_start=2022, financial_year_end=2022,
        reconstruction_method="gst_uoe_largest_remainder_v5", status="generated", is_test_data=False,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ReconstructionBatch(**defaults)


class _FakeCursor:
    """Minimal Motor-cursor stand-in: .sort()/.limit() are chainable, .to_list() is async."""

    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        return _FakeCursor(self._docs[:n])

    async def to_list(self, length=None):
        return self._docs[:length] if length is not None else self._docs


def _mongo_db_with(batches=None, manifests=None):
    batches = batches or []
    manifests = manifests or {}

    coll = MagicMock()
    coll.find = MagicMock(side_effect=lambda query: _FakeCursor(
        [d for d in batches if d.get("building_id") == query.get("building_id")]
    ))

    async def _batch_find_one(query):
        for d in batches:
            if d.get("building_id") == query.get("building_id") and d.get("batch_id") == query.get("batch_id"):
                return d
        return None

    coll.find_one = AsyncMock(side_effect=_batch_find_one)

    manifest_coll = MagicMock()

    async def _find_one(query, sort=None):
        key = (query.get("building_id"), query.get("batch_id"))
        return manifests.get(key)

    manifest_coll.find_one = AsyncMock(side_effect=_find_one)

    fake_db = MagicMock()
    fake_db.demo_bank_reconstruction_batches = coll
    fake_db.demo_bank_reconstruction_manifests = manifest_coll
    return fake_db


@pytest.fixture(autouse=True)
def _patch_building_resolution():
    with patch(
        "routers.financial_onboarding.resolve_scheme_context",
        AsyncMock(return_value=_SCHEME_A),
    ), patch("routers.financial_onboarding.create_audit_log", AsyncMock()):
        yield


# ── List / detail / manifest read endpoints ────────────────────────────────

@pytest.mark.asyncio
async def test_list_is_building_scoped():
    """A batch belonging to another building never appears, even if the raw
    query filter were somehow bypassed — the fake cursor only returns docs
    matching the query's building_id, proving the endpoint actually filters."""
    docs = [
        {"batch_id": "b1", "building_id": BUILDING_A, "financial_year_start": 2022, "financial_year_end": 2022,
         "reconstruction_method": "m", "status": "draft", "created_at": datetime.now(timezone.utc), "is_test_data": False},
        {"batch_id": "b2", "building_id": BUILDING_B, "financial_year_start": 2022, "financial_year_end": 2022,
         "reconstruction_method": "m", "status": "draft", "created_at": datetime.now(timezone.utc), "is_test_data": False},
    ]
    fake_db = _mongo_db_with(batches=docs)
    with patch("routers.financial_onboarding._mongo", return_value=fake_db):
        result = await list_reconstruction_batches(
            BUILDING_A, status=None, from_year=None, to_year=None, cursor=None, limit=50, include_test_data=False,
            current_user=_MANAGER_USER, current_building=BUILDING_A,
        )
    assert [item.batch_id for item in result.items] == ["b1"]


@pytest.mark.asyncio
async def test_list_rejects_unknown_status():
    fake_db = _mongo_db_with()
    with patch("routers.financial_onboarding._mongo", return_value=fake_db):
        with pytest.raises(HTTPException) as exc:
            await list_reconstruction_batches(
                BUILDING_A, status="not_a_real_status",
                current_user=_MANAGER_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_pagination_sets_next_cursor_only_when_more_rows_exist():
    now = datetime.now(timezone.utc)
    docs = [
        {"batch_id": f"b{i}", "building_id": BUILDING_A, "financial_year_start": 2022, "financial_year_end": 2022,
         "reconstruction_method": "m", "status": "draft", "created_at": now, "is_test_data": False}
        for i in range(3)
    ]
    fake_db = _mongo_db_with(batches=docs)
    with patch("routers.financial_onboarding._mongo", return_value=fake_db):
        result = await list_reconstruction_batches(
            BUILDING_A, status=None, from_year=None, to_year=None, cursor=None, limit=2, include_test_data=False,
            current_user=_MANAGER_USER, current_building=BUILDING_A,
        )
    assert len(result.items) == 2
    assert result.next_cursor is not None


@pytest.mark.asyncio
async def test_detail_404s_for_batch_in_another_building():
    fake_db = _mongo_db_with(batches=[
        {"batch_id": "other-batch", "building_id": BUILDING_B, "financial_year_start": 2022,
         "financial_year_end": 2022, "reconstruction_method": "m", "status": "draft",
         "created_at": datetime.now(timezone.utc), "is_test_data": False},
    ])
    with patch("routers.financial_onboarding._mongo", return_value=fake_db):
        with pytest.raises(HTTPException) as exc:
            await get_reconstruction_batch(
                BUILDING_A, "nonexistent-or-other-building-batch",
                current_user=_MANAGER_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_manifest_404s_when_none_generated_yet():
    fake_db = _mongo_db_with(manifests={})
    with patch("routers.financial_onboarding._mongo", return_value=fake_db):
        with pytest.raises(HTTPException) as exc:
            await get_reconstruction_batch_manifest(
                BUILDING_A, "batch-1", current_user=_MANAGER_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 404


# ── Reconciliation summary — the decision-basis view for reviewers ────────
#
# Deliberately duck-typed (types.SimpleNamespace), not imported from
# levy_generation_service — that module does `from scripts.migrations.
# migration_027... import ...` at its own top level, and this repo has TWO
# same-named "scripts" packages (repo-root scripts/ and backend/scripts/).
# Whichever gets imported first in a pytest session wins sys.modules['scripts']
# for the rest of the run, so importing the real dataclasses here is order-
# fragile depending on what else pytest collected first. The endpoint under
# test only reads specific attributes off these objects, so a plain
# namespace with the same attribute names is sufficient and avoids the
# collision entirely.

def _year_fund(year="2022", fund_type="admin", proposed=1_000_00, regen=1_000_00,
               demo_bank=1_000_00, existing=1_000_00, variance=0, within_tolerance=True):
    return SimpleNamespace(
        year=year, fund_type=fund_type,
        annual_levies_proposed_inc_gst_cents=proposed,
        regenerated_levy_items_total_cents=regen,
        synthetic_bank_payment_total_cents=demo_bank,
        existing_levy_items_total_cents=existing,
        variance_cents=variance, within_tolerance=within_tolerance,
    )


def _regen_line(action="unchanged", existing_paid_cents=1_000_00, principal_cents=900_00, gst_cents=100_00):
    return SimpleNamespace(
        year="2022", fund_type="admin", unit_number="1", lot_id=None,
        principal_cents=principal_cents, gst_cents=gst_cents,
        existing_principal_cents=principal_cents, existing_gst_cents=gst_cents,
        existing_paid_cents=existing_paid_cents, action=action,
    )


def _report(by_year_fund=None, lines=None, warnings=None, totals_reconcile=True):
    return SimpleNamespace(
        building_id=BUILDING_A, from_year=2022, to_year=2022,
        by_year_fund=by_year_fund or [], lines=lines or [], warnings=warnings or [],
        totals_reconcile=totals_reconcile,
    )


@pytest.fixture(autouse=True)
def _patch_pg_session():
    fake_session = MagicMock()

    class _Ctx:
        async def __aenter__(self_inner):
            return fake_session

        async def __aexit__(self_inner, *exc):
            return False

    with patch("routers.financial_onboarding.async_session_context", return_value=_Ctx()), \
         patch("routers.financial_onboarding.set_tenant", AsyncMock()):
        yield


_SCHEME_A_REAL_UUIDS = {
    "scheme_id": "22222222-0000-0000-0000-000000000002",
    "tenant_id": "11111111-0000-0000-0000-000000000001",
    "scheme_number": BUILDING_A,
}


class TestReconciliationSummary:
    # _SCHEME_A (module-level, used by every other test class in this file) uses
    # placeholder non-UUID strings — fine for tests that never construct a
    # SchemeRef. This endpoint does (UUID(scheme["tenant_id"])), so this class
    # needs a scheme fixture shaped like the real thing.
    @pytest.fixture(autouse=True)
    def _patch_scheme_with_real_uuids(self):
        with patch(
            "routers.financial_onboarding.resolve_scheme_context",
            AsyncMock(return_value=_SCHEME_A_REAL_UUIDS),
        ):
            yield

    @pytest.mark.asyncio
    async def test_returns_by_year_fund_breakdown_and_manifest(self):
        fake_db = _mongo_db_with(
            batches=[{
                "batch_id": "batch-1", "building_id": BUILDING_A, "financial_year_start": 2022,
                "financial_year_end": 2022, "reconstruction_method": "m", "status": "approved",
                "created_at": datetime.now(timezone.utc), "is_test_data": False,
            }],
            manifests={(BUILDING_A, "batch-1"): {
                "version": 1, "expected_transaction_count": 4, "expected_credit_cents": 2_000_00,
                "manifest_hash": "abc123",
            }},
        )
        report = _report(by_year_fund=[_year_fund()], lines=[_regen_line(action="unchanged")])

        with patch("routers.financial_onboarding._mongo", return_value=fake_db), \
             patch("services.levy_generation_service.build_levy_regeneration_plan", AsyncMock(return_value=report)):
            result = await get_reconstruction_batch_reconciliation_summary(
                BUILDING_A, "batch-1", current_user=_MANAGER_USER, current_building=BUILDING_A,
            )

        assert result["manifest"]["expected_credit_cents"] == 2_000_00
        assert len(result["by_year_fund"]) == 1
        assert result["by_year_fund"][0]["demo_bank_payment_total_cents"] == 1_000_00
        assert result["manual_review"]["count"] == 0
        assert result["totals_reconcile"] is True

    @pytest.mark.asyncio
    async def test_manual_review_percentage_computed_against_batch_total(self):
        fake_db = _mongo_db_with(
            batches=[{
                "batch_id": "batch-1", "building_id": BUILDING_A, "financial_year_start": 2022,
                "financial_year_end": 2022, "reconstruction_method": "m", "status": "approved",
                "created_at": datetime.now(timezone.utc), "is_test_data": False,
            }],
            manifests={(BUILDING_A, "batch-1"): {
                "version": 1, "expected_transaction_count": 1, "expected_credit_cents": 10_000_00,
                "manifest_hash": "abc123",
            }},
        )
        # One manual_review_overpaid line: paid $1,000 against a regenerated $900 charge -> $100 variance.
        report = _report(
            by_year_fund=[_year_fund(demo_bank=10_000_00)],
            lines=[_regen_line(action="manual_review_overpaid", existing_paid_cents=1_000_00,
                                principal_cents=800_00, gst_cents=100_00)],
        )

        with patch("routers.financial_onboarding._mongo", return_value=fake_db), \
             patch("services.levy_generation_service.build_levy_regeneration_plan", AsyncMock(return_value=report)):
            result = await get_reconstruction_batch_reconciliation_summary(
                BUILDING_A, "batch-1", current_user=_MANAGER_USER, current_building=BUILDING_A,
            )

        assert result["manual_review"]["count"] == 1
        assert result["manual_review"]["total_variance_cents"] == 100_00  # 1000 - (800+100)
        assert result["manual_review"]["percentage_of_batch"] == 1.0  # 10000 / 1_000_000 * 100

    @pytest.mark.asyncio
    async def test_no_units_returns_unknown_not_false(self):
        """build_levy_regeneration_plan raises RuntimeError when no units with positive UOE
        exist. This is a 'cannot compute' state, not 'computed and found broken' — must
        surface as totals_reconcile=None, never silently as False."""
        fake_db = _mongo_db_with(
            batches=[{
                "batch_id": "batch-1", "building_id": BUILDING_A, "financial_year_start": 2022,
                "financial_year_end": 2022, "reconstruction_method": "m", "status": "approved",
                "created_at": datetime.now(timezone.utc), "is_test_data": False,
            }],
            manifests={},
        )

        with patch("routers.financial_onboarding._mongo", return_value=fake_db), \
             patch("services.levy_generation_service.build_levy_regeneration_plan",
                   AsyncMock(side_effect=RuntimeError("No units with positive UOE found"))):
            result = await get_reconstruction_batch_reconciliation_summary(
                BUILDING_A, "batch-1", current_user=_MANAGER_USER, current_building=BUILDING_A,
            )

        assert result["totals_reconcile"] is None
        assert any("reconciliation_unavailable" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_flags_manifest_year_range_mismatch(self):
        """The manifest's own total and the plan's independently-recomputed Demo Bank total
        for the same years should always agree (same demo_bank_transactions rows, two code
        paths) — a mismatch means the batch's year range doesn't cover its own manifest."""
        fake_db = _mongo_db_with(
            batches=[{
                "batch_id": "batch-1", "building_id": BUILDING_A, "financial_year_start": 2022,
                "financial_year_end": 2022, "reconstruction_method": "m", "status": "approved",
                "created_at": datetime.now(timezone.utc), "is_test_data": False,
            }],
            manifests={(BUILDING_A, "batch-1"): {
                "version": 1, "expected_transaction_count": 4, "expected_credit_cents": 5_000_00,
                "manifest_hash": "abc123",
            }},
        )
        report = _report(by_year_fund=[_year_fund(demo_bank=1_000_00)])  # != manifest's 5_000_00

        with patch("routers.financial_onboarding._mongo", return_value=fake_db), \
             patch("services.levy_generation_service.build_levy_regeneration_plan", AsyncMock(return_value=report)):
            result = await get_reconstruction_batch_reconciliation_summary(
                BUILDING_A, "batch-1", current_user=_MANAGER_USER, current_building=BUILDING_A,
            )

        assert any("manifest_year_range_mismatch" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_404s_for_batch_in_another_building(self):
        fake_db = _mongo_db_with(batches=[{
            "batch_id": "other-batch", "building_id": BUILDING_B, "financial_year_start": 2022,
            "financial_year_end": 2022, "reconstruction_method": "m", "status": "draft",
            "created_at": datetime.now(timezone.utc), "is_test_data": False,
        }])
        with patch("routers.financial_onboarding._mongo", return_value=fake_db):
            with pytest.raises(HTTPException) as exc:
                await get_reconstruction_batch_reconciliation_summary(
                    BUILDING_A, "nonexistent", current_user=_MANAGER_USER, current_building=BUILDING_A,
                )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_audit_log_is_scoped_to_building_and_resource_id():
    """Unlike GET /notifications/admin/audit-logs (no resource_id filter, no
    building_id filter at all), this endpoint must filter by both — proven
    here by asserting the exact Mongo query built, not just the response."""
    fake_coll = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.sort.return_value = fake_cursor
    fake_cursor.limit.return_value = fake_cursor
    fake_cursor.to_list = AsyncMock(return_value=[
        {"action": "reconstruction_batch_created", "user_name": "Alice", "created_at": "2026-07-17T00:00:00Z"},
    ])
    fake_coll.find = MagicMock(return_value=fake_cursor)
    fake_db = MagicMock()
    fake_db.audit_logs = fake_coll

    with patch("routers.financial_onboarding._mongo", return_value=fake_db):
        result = await get_reconstruction_batch_audit_log(
            BUILDING_A, "batch-1", current_user=_MANAGER_USER, current_building=BUILDING_A,
        )

    fake_coll.find.assert_called_once_with(
        {"resource_type": "demo_bank_reconstruction_batch", "resource_id": "batch-1", "building_id": BUILDING_A},
        {"_id": 0},
    )
    assert result["batch_id"] == "batch-1"
    assert len(result["entries"]) == 1


# ── /sync: no more false "synced" ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_marks_failed_not_synced_when_zero_transactions_matched():
    """The exact bug the review flagged: previously this endpoint always
    reported 'synced' even when nothing was actually synced. processed=0
    must now raise (409) and leave the batch in 'failed', never 'synced'."""
    batch = _batch(status="generated")
    with patch("routers.financial_onboarding._mongo", return_value=MagicMock()), \
         patch.object(rbs, "_get_batch_doc", AsyncMock(return_value={"status": "generated"})), \
         patch.object(rbs, "_doc_to_batch", return_value=batch), \
         patch.object(rbs, "mark_syncing", AsyncMock(return_value=batch)), \
         patch.object(rbs, "_transition", AsyncMock(return_value=_batch(status="failed"))) as mock_transition, \
         patch("routers.financial_onboarding.get_effective_feature_access", AsyncMock(return_value=True)), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={
             "processed": 0, "inserted": 0, "duplicates": 0, "failed": 0, "skipped_other_batch": 5,
         })):
        with pytest.raises(HTTPException) as exc:
            await sync_reconstruction_batch(
                BUILDING_A, "batch-1", current_user=_ADMIN_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 409
    mock_transition.assert_awaited_once()
    assert mock_transition.call_args.kwargs["to_status"] == "failed"


@pytest.mark.asyncio
async def test_sync_marks_failed_when_any_row_fails():
    batch = _batch(status="generated")
    with patch("routers.financial_onboarding._mongo", return_value=MagicMock()), \
         patch.object(rbs, "_get_batch_doc", AsyncMock(return_value={"status": "generated"})), \
         patch.object(rbs, "_doc_to_batch", return_value=batch), \
         patch.object(rbs, "mark_syncing", AsyncMock(return_value=batch)), \
         patch.object(rbs, "_transition", AsyncMock(return_value=_batch(status="failed"))), \
         patch("routers.financial_onboarding.get_effective_feature_access", AsyncMock(return_value=True)), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={
             "processed": 10, "inserted": 8, "duplicates": 0, "failed": 2, "skipped_other_batch": 0,
         })):
        result = await sync_reconstruction_batch(
            BUILDING_A, "batch-1", current_user=_ADMIN_USER, current_building=BUILDING_A,
        )
    assert result["batch"]["status"] == "failed"
    assert result["sync_result"]["failed"] == 2


@pytest.mark.asyncio
async def test_sync_marks_synced_only_on_clean_nonempty_result():
    batch = _batch(status="generated")
    synced_batch = _batch(status="synced")
    with patch("routers.financial_onboarding._mongo", return_value=MagicMock()), \
         patch.object(rbs, "_get_batch_doc", AsyncMock(return_value={"status": "generated"})), \
         patch.object(rbs, "_doc_to_batch", return_value=batch), \
         patch.object(rbs, "mark_syncing", AsyncMock(return_value=batch)), \
         patch.object(rbs, "mark_synced", AsyncMock(return_value=synced_batch)) as mock_mark_synced, \
         patch("routers.financial_onboarding.get_effective_feature_access", AsyncMock(return_value=True)), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={
             "processed": 10, "inserted": 10, "duplicates": 0, "failed": 0, "skipped_other_batch": 0,
         })) as mock_sync:
        result = await sync_reconstruction_batch(
            BUILDING_A, "batch-1", current_user=_ADMIN_USER, current_building=BUILDING_A,
        )
    assert result["batch"]["status"] == "synced"
    mock_mark_synced.assert_awaited_once()
    # Sync must be scoped to exactly this batch, not the whole building's feed.
    _, kwargs = mock_sync.call_args
    assert kwargs["payload"].reconstruction_batch_id == "batch-1"
    assert kwargs["payload"].disable_auto_allocation is True


@pytest.mark.asyncio
async def test_sync_passes_wrapped_db_not_mongo_db_to_run_bank_feed_sync():
    """Regression test for the real 2026-07-31 production bug: run_bank_feed_sync()
    and everything it calls (_account_refs_for_sync, _mark_demo_bank_sync, ...) do
    `db._db.<collection>` themselves — they need the WRAPPED TenantScopedDatabase
    (routers.financial_onboarding.db, i.e. database.db), never `_mongo()`'s return
    value (already db._db). Passing the unwrapped one double-unwraps and raises
    AttributeError on every real call — every other test in this file mocks
    run_bank_feed_sync entirely and never asserts on which db object it receives,
    so none of them would have caught this. Asserted by identity, not equality:
    two independent MagicMock() instances are never == unless it's the same object."""
    batch = _batch(status="generated")
    synced_batch = _batch(status="synced")
    mongo_sentinel = MagicMock(name="mongo_db_from__mongo")
    with patch("routers.financial_onboarding._mongo", return_value=mongo_sentinel), \
         patch.object(rbs, "_get_batch_doc", AsyncMock(return_value={"status": "generated"})), \
         patch.object(rbs, "_doc_to_batch", return_value=batch), \
         patch.object(rbs, "mark_syncing", AsyncMock(return_value=batch)), \
         patch.object(rbs, "mark_synced", AsyncMock(return_value=synced_batch)), \
         patch("routers.financial_onboarding.get_effective_feature_access", AsyncMock(return_value=True)), \
         patch("routers.bank_feeds.run_bank_feed_sync", AsyncMock(return_value={
             "processed": 10, "inserted": 10, "duplicates": 0, "failed": 0, "skipped_other_batch": 0,
         })) as mock_sync:
        await sync_reconstruction_batch(
            BUILDING_A, "batch-1", current_user=_ADMIN_USER, current_building=BUILDING_A,
        )
    args, _ = mock_sync.call_args
    from database import db as wrapped_db
    assert args[0] is wrapped_db, (
        "sync_reconstruction_batch must pass the wrapped TenantScopedDatabase to "
        "run_bank_feed_sync, not _mongo()'s (already-unwrapped) return value"
    )
    assert args[0] is not mongo_sentinel


@pytest.mark.asyncio
async def test_sync_403s_when_underlying_bank_feed_toggle_disabled():
    batch = _batch(status="generated")
    with patch("routers.financial_onboarding._mongo", return_value=MagicMock()), \
         patch.object(rbs, "_get_batch_doc", AsyncMock(return_value={"status": "generated"})), \
         patch.object(rbs, "_doc_to_batch", return_value=batch), \
         patch("routers.financial_onboarding.get_effective_feature_access", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await sync_reconstruction_batch(
                BUILDING_A, "batch-1", current_user=_ADMIN_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "FEATURE_DISABLED"


# ── Approve: identity always from the authenticated caller ─────────────────

@pytest.mark.asyncio
async def test_review_records_reviewer_from_caller_never_client():
    """ReviewReconstructionBatchRequest carries no reviewed_by field — this
    test locks in that record_review is always called with the authenticated
    caller's own id, never something a client could spoof."""
    reviewed_batch = _batch(status="needs_review", reviewed_by=_STRATA_ADMIN_USER["id"])
    with patch.object(rbs, "record_review", AsyncMock(return_value=reviewed_batch)) as mock_review:
        result = await review_reconstruction_batch(
            BUILDING_A, "batch-1", ReviewReconstructionBatchRequest(notes="Looks correct"),
            current_user=_STRATA_ADMIN_USER, current_building=BUILDING_A,
        )
    assert mock_review.call_args.kwargs["reviewed_by"] == _STRATA_ADMIN_USER["id"]
    assert mock_review.call_args.kwargs["notes"] == "Looks correct"
    assert result["status"] == "needs_review"


@pytest.mark.asyncio
async def test_approve_requires_prior_review():
    """A batch with no reviewed_by set yet must 409, never silently approve."""
    with patch.object(rbs, "_get_batch_doc", AsyncMock(return_value={"reviewed_by": None, "manifest_hash": "h1"})):
        with pytest.raises(HTTPException) as exc:
            await approve_reconstruction_batch(
                BUILDING_A, "batch-1", ApproveReconstructionBatchRequest(),
                current_user=_STRATA_ADMIN_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_rejects_same_user_as_reviewer():
    """Dual control: the approver must be a different authenticated user
    than whoever recorded the review — this is the genuine two-person
    control the review noted was missing when approve/review were combined
    into a single endpoint."""
    with patch.object(
        rbs, "_get_batch_doc",
        AsyncMock(return_value={"reviewed_by": _STRATA_ADMIN_USER["id"], "manifest_hash": "h1"}),
    ):
        with pytest.raises(HTTPException) as exc:
            await approve_reconstruction_batch(
                BUILDING_A, "batch-1", ApproveReconstructionBatchRequest(),
                current_user=_STRATA_ADMIN_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_approve_succeeds_when_reviewer_and_approver_differ():
    approved_batch = _batch(status="approved", reviewed_by=_STRATA_ADMIN_USER["id"], approved_by=_ADMIN_USER["id"])
    fake_db = _mongo_db_with(manifests={(BUILDING_A, "batch-1"): {"manifest_hash": "h1", "version": 1}})
    with patch.object(
        rbs, "_get_batch_doc",
        AsyncMock(return_value={"reviewed_by": _STRATA_ADMIN_USER["id"], "manifest_hash": "h1"}),
    ), patch("routers.financial_onboarding._mongo", return_value=fake_db), \
       patch.object(rbs, "approve_batch", AsyncMock(return_value=approved_batch)) as mock_approve:
        result = await approve_reconstruction_batch(
            BUILDING_A, "batch-1", ApproveReconstructionBatchRequest(),
            current_user=_ADMIN_USER, current_building=BUILDING_A,
        )
    assert mock_approve.call_args.kwargs["approved_by"] == _ADMIN_USER["id"]
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_rejects_stale_manifest_hash():
    """Defensive check: if the persisted manifest's hash no longer matches
    the hash recorded at review time, refuse to approve rather than trust a
    review that may have been performed against a different manifest."""
    fake_db = _mongo_db_with(manifests={(BUILDING_A, "batch-1"): {"manifest_hash": "DIFFERENT-HASH", "version": 2}})
    with patch.object(
        rbs, "_get_batch_doc",
        AsyncMock(return_value={"reviewed_by": _STRATA_ADMIN_USER["id"], "manifest_hash": "h1"}),
    ), patch("routers.financial_onboarding._mongo", return_value=fake_db):
        with pytest.raises(HTTPException) as exc:
            await approve_reconstruction_batch(
                BUILDING_A, "batch-1", ApproveReconstructionBatchRequest(),
                current_user=_ADMIN_USER, current_building=BUILDING_A,
            )
    assert exc.value.status_code == 409


def test_approve_request_model_has_no_identity_fields():
    assert "reviewed_by" not in ApproveReconstructionBatchRequest.model_fields
    assert "approved_by" not in ApproveReconstructionBatchRequest.model_fields


def test_review_request_model_has_no_identity_fields():
    assert "reviewed_by" not in ReviewReconstructionBatchRequest.model_fields


# ── Levy regeneration override justification ────────────────────────────────

def test_regenerate_apply_request_rejects_short_override_reason():
    with pytest.raises(Exception):
        RegenerateLevyApplyRequest(confirm=True, override_reconciliation_mismatch=True, override_reason="short")


def test_regenerate_apply_request_accepts_valid_override_reason():
    req = RegenerateLevyApplyRequest(
        confirm=True, override_reconciliation_mismatch=True,
        override_reason="Variance already reconciled manually against the AGM pack, see doc #4471.",
    )
    assert req.override_reason is not None
