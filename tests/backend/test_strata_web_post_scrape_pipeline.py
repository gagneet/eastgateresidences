# @featuretrace:strata-web-portal-finance-ingest — Tests for the post-scrape orchestration script.
# Layer: test
# Data flow: strata_web_post_scrape_pipeline.main() -> strata_web_portal_ingest.run (mocked)
#            -> derive_strata_web_balance_delta_transactions (mocked) -> report dict.
# Scope: building-scoped
# Related: backend/scripts/ingest/strata_web_post_scrape_pipeline.py
"""Regression tests for the post-scrape pipeline.

These exist because the script shipped WITHOUT tests and failed on its first real
``--apply`` run with two defects that a single test would have caught:

1. It passed ``db._db`` (the raw ``AsyncDatabase``) to
   ``derive_strata_web_balance_delta_transactions``. That function's downstream
   ``integrations/demo_bank/ingestion.py::_upsert_transaction`` reaches through
   ``db._db.demo_bank_transactions``, so it needs the ``TenantScopedDatabase``
   WRAPPER — the raw handle raised
   ``AttributeError("AsyncDatabase has no attribute '_db'")``.
2. It read the staged unit count from ``result["snapshot"]``, a key that only
   exists on the DRY-RUN path. A successful 87-unit apply reported ``0``.

Both are shape-of-the-contract mistakes, which is exactly what a test pins down.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

BUILDING = "13195"


def _patch_module(monkeypatch, dotted: str, replacement) -> None:
    """Replace a module for the duration of a test, robustly.

    `monkeypatch.setitem(sys.modules, ...)` alone is NOT enough for a SUBMODULE.
    The pipeline does `from scripts.ingest import strata_web_portal_ingest`, and once
    any earlier test has imported the real submodule, that form resolves via the
    PACKAGE ATTRIBUTE — `scripts.ingest.strata_web_portal_ingest` — not via
    `sys.modules`. The patch is then silently ignored and the real `run()` executes
    against the live database.

    That made these tests pass alone and fail intermittently in the full suite,
    depending purely on whether something else had imported the submodule first.
    Patching both places removes the ordering dependency.
    """
    monkeypatch.setitem(sys.modules, dotted, replacement)
    if "." in dotted:
        parent_name, _, attr = dotted.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            monkeypatch.setattr(parent, attr, replacement, raising=False)


def _snapshot_rows(dates: list[str], label: str = "2026") -> list[dict]:
    return [
        {"building_id": BUILDING, "financial_year": label, "snapshot_date": d}
        for d in dates
    ]


def _fake_wrapper(rows: list[dict]) -> MagicMock:
    """A stand-in for ``TenantScopedDatabase`` — note it HAS a ``_db`` attribute.

    That is the whole point: the assertion below checks the object handed to the
    inference service carries ``_db``, because its downstream writer dereferences it.
    """
    raw = MagicMock()

    def _find(query, *args, **kwargs):
        bid = query.get("building_id")
        cursor = MagicMock()
        cursor.to_list = AsyncMock(
            return_value=[r for r in rows if bid is None or r["building_id"] == bid]
        )
        return cursor

    raw.staging_strata_web_snapshots.find = MagicMock(side_effect=_find)
    # Catch-up (the default) resolves each window's newer snapshot by date to pass its
    # _id through as current_snapshot_id, so the fixture must serve find_one too.
    raw.staging_strata_web_snapshots.find_one = AsyncMock(
        side_effect=lambda q, *a, **k: {"_id": f"id-{q.get('snapshot_date')}"}
    )
    wrapper = MagicMock()
    wrapper._db = raw
    return wrapper


@pytest.mark.asyncio
async def test_inference_receives_the_wrapper_not_the_raw_handle(monkeypatch):
    """The regression: the service must get an object exposing ``_db``."""
    import scripts.ingest.strata_web_post_scrape_pipeline as pipeline

    wrapper = _fake_wrapper(_snapshot_rows(["2026-08-06", "2026-08-19"]))
    _patch_module(monkeypatch, "database", MagicMock(db=wrapper))
    monkeypatch.setattr(pipeline, "__name__", pipeline.__name__)

    captured: dict = {}

    async def fake_derive(**kwargs):
        captured.update(kwargs)
        return {"candidates_created": 3, "candidates_skipped": 0, "warnings": []}

    fake_service = MagicMock(derive_strata_web_balance_delta_transactions=fake_derive)
    _patch_module(monkeypatch, "services.strata_web_balance_inference_service", fake_service
    )

    async def fake_run(**kwargs):
        return {"dry_run": False, "upserted": {}, "replaced_existing": False,
                "per_unit_count": 87, "pg_dual_write": {"attempted": True}}

    _patch_module(monkeypatch, "scripts.ingest.strata_web_portal_ingest", MagicMock(run=fake_run),
    )
    _patch_module(monkeypatch, "request_context", MagicMock(set_ctx_building_id=lambda _b: None))
    monkeypatch.setattr(
        sys, "argv",
        ["p", "--building-id", BUILDING, "--financial-year", "2026", "--apply"],
    )

    await pipeline.main()

    assert "db" in captured, "the inference service was never called"
    # Asserted on the DEFAULT (catch-up) path, since that is what production runs.
    assert hasattr(captured["db"], "_db"), (
        "the service must receive the TenantScopedDatabase wrapper — its downstream "
        "_upsert_transaction dereferences db._db"
    )
    assert captured["db"] is wrapper


@pytest.mark.asyncio
async def test_apply_path_reports_the_real_unit_count(monkeypatch, capsys):
    """``run()`` returns ``per_unit_count`` on apply and ``snapshot`` on dry-run.
    Reading only ``snapshot`` reported 0 units on a successful 87-unit apply."""
    import scripts.ingest.strata_web_post_scrape_pipeline as pipeline

    wrapper = _fake_wrapper(_snapshot_rows(["2026-08-06", "2026-08-19"]))
    _patch_module(monkeypatch, "database", MagicMock(db=wrapper))

    async def fake_derive(**kwargs):
        return {"candidates_created": 0, "candidates_skipped": 0, "warnings": []}

    _patch_module(monkeypatch, "services.strata_web_balance_inference_service", MagicMock(derive_strata_web_balance_delta_transactions=fake_derive),
    )

    async def fake_run(**kwargs):
        return {"dry_run": False, "upserted": {}, "replaced_existing": False,
                "per_unit_count": 87, "pg_dual_write": {"attempted": True}}

    _patch_module(monkeypatch, "scripts.ingest.strata_web_portal_ingest", MagicMock(run=fake_run),
    )
    _patch_module(monkeypatch, "request_context", MagicMock(set_ctx_building_id=lambda _b: None))
    monkeypatch.setattr(
        sys, "argv",
        ["p", "--building-id", BUILDING, "--financial-year", "2026", "--apply", "--latest-only"],
    )

    await pipeline.main()
    out = capsys.readouterr().out
    assert '"per_unit_balance_count": 87' in out, (
        f"apply path must report the real count, not 0. Got:\n{out[:600]}"
    )


@pytest.mark.asyncio
async def test_single_snapshot_skips_inference_with_a_count(monkeypatch, capsys):
    """One snapshot cannot produce a delta — the no-op must name the count."""
    import scripts.ingest.strata_web_post_scrape_pipeline as pipeline

    wrapper = _fake_wrapper(_snapshot_rows(["2026-08-19"]))
    _patch_module(monkeypatch, "database", MagicMock(db=wrapper))

    called = {"n": 0}

    async def fake_derive(**kwargs):
        called["n"] += 1
        return {}

    _patch_module(monkeypatch, "services.strata_web_balance_inference_service", MagicMock(derive_strata_web_balance_delta_transactions=fake_derive),
    )
    _patch_module(monkeypatch, "scripts.ingest.strata_web_portal_ingest", MagicMock(run=AsyncMock(return_value={"dry_run": False, "upserted": {},
                                              "replaced_existing": False,
                                              "per_unit_count": 87,
                                              "pg_dual_write": {}})),
    )
    _patch_module(monkeypatch, "request_context", MagicMock(set_ctx_building_id=lambda _b: None))
    monkeypatch.setattr(
        sys, "argv",
        ["p", "--building-id", BUILDING, "--financial-year", "2026", "--apply"],
    )

    await pipeline.main()
    out = capsys.readouterr().out
    assert called["n"] == 0, "must not attempt inference with a single snapshot"
    assert "needs TWO snapshots" in out


@pytest.mark.asyncio
async def test_mismatched_labels_still_pair(monkeypatch, capsys):
    """'2025-2026' and '2026' name the same year and must both count toward the pair."""
    import scripts.ingest.strata_web_post_scrape_pipeline as pipeline

    rows = (_snapshot_rows(["2026-08-06"], label="2025-2026")
            + _snapshot_rows(["2026-08-19"], label="2026"))
    wrapper = _fake_wrapper(rows)
    _patch_module(monkeypatch, "database", MagicMock(db=wrapper))

    called = {"n": 0}

    async def fake_derive(**kwargs):
        called["n"] += 1
        return {"candidates_created": 2, "candidates_skipped": 0, "warnings": []}

    _patch_module(monkeypatch, "services.strata_web_balance_inference_service", MagicMock(derive_strata_web_balance_delta_transactions=fake_derive),
    )
    _patch_module(monkeypatch, "scripts.ingest.strata_web_portal_ingest", MagicMock(run=AsyncMock(return_value={"dry_run": False, "upserted": {},
                                              "replaced_existing": False,
                                              "per_unit_count": 87,
                                              "pg_dual_write": {}})),
    )
    _patch_module(monkeypatch, "request_context", MagicMock(set_ctx_building_id=lambda _b: None))
    monkeypatch.setattr(
        sys, "argv",
        ["p", "--building-id", BUILDING, "--financial-year", "2026", "--apply"],
    )

    await pipeline.main()
    assert called["n"] == 1, "differently-labelled snapshots of the same year must pair"


@pytest.mark.asyncio
async def test_catch_up_processes_every_window_not_just_the_latest(monkeypatch, capsys):
    """A skipped window must be picked up, not stranded — and this is the DEFAULT.

    `derive_strata_web_balance_delta_transactions` compares the most recent snapshot
    against the one immediately before it. It has no concept of "windows I have not
    processed", so a missed run loses that window's movement silently — no error, no
    backlog, and the NEXT run reports success because its own window is fine.

    East Gate hit exactly this: the 2026-08-06 -> 2026-08-19 window was never
    processed, stranding 21 lots' movement worth $15,566.04 of payments — including
    the $300 on lot 5 that prompted the investigation. The 08-19 -> 08-28 run then
    reported success.
    """
    import scripts.ingest.strata_web_post_scrape_pipeline as pipeline

    rows = (_snapshot_rows(["2026-08-06"]) + _snapshot_rows(["2026-08-19"])
            + _snapshot_rows(["2026-08-28"]))
    wrapper = _fake_wrapper(rows)
    # catch-up resolves each window's newer snapshot by date to pass its _id through.
    wrapper._db.staging_strata_web_snapshots.find_one = AsyncMock(
        side_effect=lambda q, *a, **k: {"_id": f"id-{q.get('snapshot_date')}"}
    )
    _patch_module(monkeypatch, "database", MagicMock(db=wrapper))

    seen: list[str] = []

    async def fake_derive(**kwargs):
        seen.append(kwargs.get("current_snapshot_id"))
        return {"candidates_created": 5, "candidates_skipped": 0, "warnings": []}

    _patch_module(monkeypatch, "services.strata_web_balance_inference_service", MagicMock(derive_strata_web_balance_delta_transactions=fake_derive),
    )
    _patch_module(monkeypatch, "request_context", MagicMock(set_ctx_building_id=lambda _b: None))
    monkeypatch.setattr(
        sys, "argv",
        ["p", "--building-id", BUILDING, "--financial-year", "2026",
         "--skip-staging", "--apply"],   # catch-up is the DEFAULT now
    )

    await pipeline.main()

    # Three snapshots => TWO consecutive windows, both processed, oldest first.
    assert len(seen) == 2, f"every consecutive window must be processed, got {seen}"
    assert seen == ["id-2026-08-19", "id-2026-08-28"], (
        f"windows must run oldest-first so a skipped one is caught up: {seen}"
    )
    out = capsys.readouterr().out
    assert "2026-08-06 -> 2026-08-19" in out, "each window's result must be reported separately"
    assert "2026-08-19 -> 2026-08-28" in out
