# @featuretrace:financial-onboarding — Tests for
#   scripts/reconcile_scraped_financials_to_categories.py, the review step that bridges a
#   Strata Sync scrape (strata_financials) into levy_categories for buildings where
#   disable_strata_sync_direct_write blocks the scraper's own direct write.
# Layer: test
# Related: backend/scripts/reconcile_scraped_financials_to_categories.py
"""Unit tests, all DB interaction mocked. Covers:
1. _diff() correctly classifies NEW / UPDATE / UNCHANGED / SKIPPED per category.
2. Fund-type translation (capital_works -> sinking) for the gap-tolerant importer.
3. --apply requires --authorised-by/--executed-by, and they must differ (dual control).
4. --apply is never reached in preview mode — import_gap_tolerant_financial_data is not called.
5. financial_year auto-detection: single value auto-picked; multiple values raise.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.reconcile_scraped_financials_to_categories import (  # noqa: E402
    _diff,
    _resolve_canonical,
    _resolve_scraped_financial_year,
    run,
)


_VALID_AUTHORISER = {"id": "uuid-a", "role": "strata_admin", "is_active": True, "full_name": "A Reviewer"}
_VALID_EXECUTOR = {"id": "uuid-b", "role": "strata_manager", "is_active": True, "full_name": "B Executor"}


def _mock_mdb(
    *, scraped: list[dict], stored: list[dict], financial_years: list[str] | None = None,
    users_by_id: dict[str, dict] | None = None,
):
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    strata_financials.distinct = AsyncMock(
        return_value=financial_years if financial_years is not None else ["2026-2027"]
    )

    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter(stored))

    # Defaults to the two valid dual-control identities used across the --apply tests
    # below; pass users_by_id={} (or a custom map) to exercise rejection paths.
    resolved_users = (
        users_by_id if users_by_id is not None
        else {"uuid-a": _VALID_AUTHORISER, "uuid-b": _VALID_EXECUTOR}
    )
    users = MagicMock()
    users.find_one = AsyncMock(side_effect=lambda query, *a, **kw: resolved_users.get(query.get("id")))

    def getitem(name):
        return {
            "strata_financials": strata_financials, "levy_categories": levy_categories, "users": users,
        }[name]

    mdb.__getitem__ = MagicMock(side_effect=getitem)
    return mdb


def _cursor(items):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=items)
    return c


async def _async_iter(items):
    for item in items:
        yield item


def _async_iter_factory(items):
    class _Cursor:
        def __aiter__(self):
            return _async_iter(items)
    return _Cursor()


@pytest.mark.asyncio
async def test_diff_classifies_new_update_unchanged():
    scraped = [
        {"category": "Cleaning", "fund": "admin", "actual": 17468.73},
        {"category": "Roof Repairs", "fund": "capital_works", "actual": 69195.0},
        {"category": "Audit Fees", "fund": "admin", "actual": 730.0},
        {"category": "Waterproofing", "fund": "admin", "actual": 18990.0},
    ]
    stored = [
        {"name": "Cleaning", "actual_amount": 6678.73, "fund_type": "admin"},
        {"name": "Roof Repairs", "actual_amount": 68160.0, "fund_type": "sinking"},
        {"name": "Audit Fees", "actual_amount": 730.0, "fund_type": "admin"},
    ]
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter_factory(stored))

    def getitem(name):
        return {"strata_financials": strata_financials, "levy_categories": levy_categories}[name]
    mdb.__getitem__ = MagicMock(side_effect=getitem)

    rows = await _diff(mdb, "13195", "2026", "2026-2027")
    by_cat = {r["category"]: r for r in rows}

    assert by_cat["Cleaning"]["action"] == "UPDATE"
    assert by_cat["Cleaning"]["delta"] == round(17468.73 - 6678.73, 2)
    assert by_cat["Roof Repairs"]["action"] == "UPDATE"
    assert by_cat["Roof Repairs"]["fund"] == "sinking"  # capital_works -> sinking translation
    assert by_cat["Audit Fees"]["action"] == "UNCHANGED"
    assert by_cat["Waterproofing"]["action"] == "NEW"
    assert by_cat["Waterproofing"]["stored_actual"] is None


@pytest.mark.asyncio
async def test_diff_skips_unrecognised_fund_type():
    scraped = [{"category": "Mystery Fund Item", "fund": "trust", "actual": 100.0}]
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter_factory([]))

    def getitem(name):
        return {"strata_financials": strata_financials, "levy_categories": levy_categories}[name]
    mdb.__getitem__ = MagicMock(side_effect=getitem)

    rows = await _diff(mdb, "13195", "2026", "2026-2027")
    assert rows[0]["action"] == "SKIPPED"
    assert "trust" in rows[0]["reason"]


@pytest.mark.asyncio
async def test_diff_within_tolerance_is_unchanged_not_update():
    """A one-cent rounding difference must not be reported as a real change."""
    scraped = [{"category": "Audit Fees", "fund": "admin", "actual": 730.005}]
    stored = [{"name": "Audit Fees", "actual_amount": 730.0, "fund_type": "admin"}]
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter_factory(stored))

    def getitem(name):
        return {"strata_financials": strata_financials, "levy_categories": levy_categories}[name]
    mdb.__getitem__ = MagicMock(side_effect=getitem)

    rows = await _diff(mdb, "13195", "2026", "2026-2027")
    assert rows[0]["action"] == "UNCHANGED"


@pytest.mark.asyncio
async def test_diff_flags_archived_category_conflict_instead_of_comparing_to_stale_data():
    """Real East Gate bug (found during 2026-08-19 self-audit): a scraped category name
    that matches only an ARCHIVED levy_categories doc (consolidated into a differently
    named canonical category) must never be compared against that stale archived
    actual_amount, and must never be --apply-eligible as NEW/UPDATE — both would
    silently undo consolidate_levy_categories_canonical.py's cleanup. It must instead be
    flagged for human review, pointing at the live canonical doc's current figure."""
    scraped = [{"category": "Tax Agent Fees - BAS/GST", "fund": "admin", "actual": 200.0}]
    archived_doc = {
        "id": "archived-1", "name": "Tax Agent Fees - BAS/GST", "actual_amount": 100.0,
        "fund_type": "admin", "is_archived": True, "merged_into": "canonical-1",
    }
    canonical_doc = {"id": "canonical-1", "name": "GST Administration", "actual_amount": 225.28}

    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter_factory([archived_doc]))
    levy_categories.find_one = AsyncMock(return_value=canonical_doc)

    def getitem(name):
        return {"strata_financials": strata_financials, "levy_categories": levy_categories}[name]
    mdb.__getitem__ = MagicMock(side_effect=getitem)

    rows = await _diff(mdb, "13195", "2026", "2026-2027")

    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "ARCHIVED_CONFLICT"
    assert row["stored_actual"] is None  # never compared against the stale $100 archived figure
    assert "GST Administration" in row["reason"]
    assert "225.28" in row["reason"]
    levy_categories.find_one.assert_awaited_once_with(
        {"id": "canonical-1", "building_id": "13195"}, {"_id": 0}
    )


@pytest.mark.asyncio
async def test_diff_flags_duplicate_live_category_instead_of_silently_picking_one():
    """Two undifferentiated, non-archived, SAME-FUND docs sharing a category name for
    2026 — a genuine DB data-integrity issue with no single record --apply could safely
    target. The previous version of _diff() silently kept whichever one Mongo's find()
    cursor happened to iterate last, with no warning; this must instead be flagged.

    (Not East Gate's real "Building Repairs & Maintenance" case, corrected 2026-08-19 —
    that pair turned out to be legitimately one admin-fund doc and one sinking-fund doc,
    not a duplicate at all, once the grouping key became fund-aware. This fixture uses
    the same name but pins BOTH docs to the same fund_type to exercise a genuine
    same-fund duplicate.)"""
    scraped = [{"category": "Building Repairs & Maintenance", "fund": "admin", "actual": 10456.44}]
    stored = [
        {"name": "Building Repairs & Maintenance", "actual_amount": 5149.17, "fund_type": "admin"},
        {"name": "Building Repairs & Maintenance", "actual_amount": 0.0, "fund_type": "admin"},
    ]
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter_factory(stored))

    def getitem(name):
        return {"strata_financials": strata_financials, "levy_categories": levy_categories}[name]
    mdb.__getitem__ = MagicMock(side_effect=getitem)

    rows = await _diff(mdb, "13195", "2026", "2026-2027")

    assert len(rows) == 1
    assert rows[0]["action"] == "DUPLICATE_LIVE_CATEGORY"
    assert rows[0]["stored_actual"] is None


@pytest.mark.asyncio
async def test_same_name_different_fund_is_not_flagged_as_duplicate():
    """Real East Gate bug (found during 2026-08-19 self-audit, AFTER the
    ARCHIVED_CONFLICT fix shipped): a first version of the grouping key was name-only,
    so East Gate's real "Building Repairs & Maintenance" — one legitimate admin-fund doc
    ($5,149.17) and one legitimate sinking-fund $0.00 placeholder doc — was wrongly
    flagged DUPLICATE_LIVE_CATEGORY. A category name can legitimately exist once per
    fund; the key must be (name, fund_type), and the scraped row's own fund must match
    against only the SAME-fund stored doc, producing a plain UPDATE."""
    scraped = [{"category": "Building Repairs & Maintenance", "fund": "admin", "actual": 10456.44}]
    stored = [
        {"name": "Building Repairs & Maintenance", "actual_amount": 5149.17, "fund_type": "admin"},
        {"name": "Building Repairs & Maintenance", "actual_amount": 0.0, "fund_type": "sinking"},
    ]
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.find = MagicMock(return_value=_cursor(scraped))
    levy_categories = MagicMock()
    levy_categories.find = MagicMock(return_value=_async_iter_factory(stored))

    def getitem(name):
        return {"strata_financials": strata_financials, "levy_categories": levy_categories}[name]
    mdb.__getitem__ = MagicMock(side_effect=getitem)

    rows = await _diff(mdb, "13195", "2026", "2026-2027")

    assert len(rows) == 1
    assert rows[0]["action"] == "UPDATE"
    assert rows[0]["stored_actual"] == 5149.17
    assert rows[0]["delta"] == round(10456.44 - 5149.17, 2)


@pytest.mark.asyncio
async def test_resolve_canonical_follows_single_hop():
    archived_doc = {"id": "a1", "name": "Old Name", "is_archived": True, "merged_into": "c1"}
    canonical_doc = {"id": "c1", "name": "New Name", "is_archived": None}
    mdb = MagicMock()
    levy_categories = MagicMock()
    levy_categories.find_one = AsyncMock(return_value=canonical_doc)
    mdb.__getitem__ = MagicMock(return_value=levy_categories)

    result = await _resolve_canonical(mdb, "13195", archived_doc)
    assert result == canonical_doc


@pytest.mark.asyncio
async def test_resolve_canonical_returns_none_on_broken_chain():
    """merged_into points at an id that no longer resolves to any doc."""
    archived_doc = {"id": "a1", "name": "Old Name", "is_archived": True, "merged_into": "missing-id"}
    mdb = MagicMock()
    levy_categories = MagicMock()
    levy_categories.find_one = AsyncMock(return_value=None)
    mdb.__getitem__ = MagicMock(return_value=levy_categories)

    result = await _resolve_canonical(mdb, "13195", archived_doc)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_canonical_returns_none_on_self_referential_cycle():
    """merged_into points back at itself — must not infinite-loop."""
    archived_doc = {"id": "a1", "name": "Old Name", "is_archived": True, "merged_into": "a1"}
    mdb = MagicMock()
    levy_categories = MagicMock()
    levy_categories.find_one = AsyncMock(return_value={
        "id": "a1", "name": "Old Name", "is_archived": True, "merged_into": "a1",
    })
    mdb.__getitem__ = MagicMock(return_value=levy_categories)

    result = await _resolve_canonical(mdb, "13195", archived_doc)
    assert result is None


@pytest.mark.asyncio
async def test_apply_never_includes_archived_conflict_or_duplicate_rows():
    """End-to-end: even with --apply, flagged categories must never reach the CSV fed
    to import_gap_tolerant_financial_data — only genuine NEW/UPDATE rows can."""
    scraped = [
        {"category": "Cleaning", "fund": "admin", "actual": 17468.73},  # genuine UPDATE
        {"category": "Tax Agent Fees - BAS/GST", "fund": "admin", "actual": 200.0},  # ARCHIVED_CONFLICT
        {"category": "Building Repairs & Maintenance", "fund": "admin", "actual": 10456.44},  # DUPLICATE
    ]
    stored = [
        {"name": "Cleaning", "actual_amount": 6678.73, "fund_type": "admin"},
        {"name": "Tax Agent Fees - BAS/GST", "actual_amount": 100.0, "fund_type": "admin",
         "is_archived": True, "merged_into": "c1"},
        {"name": "Building Repairs & Maintenance", "actual_amount": 5149.17, "fund_type": "admin"},
        {"name": "Building Repairs & Maintenance", "actual_amount": 0.0, "fund_type": "admin"},
    ]
    canonical_doc = {"id": "c1", "name": "GST Administration", "actual_amount": 225.28}

    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=scraped, stored=stored)
        mdb["levy_categories"].find_one = AsyncMock(return_value=canonical_doc)
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)
        mock_import.return_value = {"ok": True}

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by="uuid-b")

    assert exit_code == 0
    mock_import.assert_awaited_once()
    content = mock_import.call_args.kwargs["content"].decode("utf-8")
    assert "Cleaning" in content
    assert "Tax Agent Fees" not in content
    assert "Building Repairs" not in content


@pytest.mark.asyncio
async def test_resolve_financial_year_auto_detects_single_value():
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.distinct = AsyncMock(return_value=["2026-2027"])
    mdb.__getitem__ = MagicMock(return_value=strata_financials)

    result = await _resolve_scraped_financial_year(mdb, "13195")
    assert result == "2026-2027"


@pytest.mark.asyncio
async def test_resolve_financial_year_raises_on_ambiguity():
    mdb = MagicMock()
    strata_financials = MagicMock()
    strata_financials.distinct = AsyncMock(return_value=["2025-2026", "2026-2027"])
    mdb.__getitem__ = MagicMock(return_value=strata_financials)

    with pytest.raises(SystemExit):
        await _resolve_scraped_financial_year(mdb, "13195")


@pytest.mark.asyncio
async def test_preview_mode_never_calls_the_importer():
    scraped = [{"category": "Cleaning", "fund": "admin", "actual": 17468.73}]
    stored = [{"name": "Cleaning", "actual_amount": 6678.73}]

    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=scraped, stored=stored)
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=False, authorised_by=None, executed_by=None)

    assert exit_code == 0
    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_apply_requires_both_identities():
    scraped = [{"category": "Cleaning", "fund": "admin", "actual": 17468.73}]
    stored = [{"name": "Cleaning", "actual_amount": 6678.73}]

    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=scraped, stored=stored)
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by=None)

    assert exit_code == 2
    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_apply_rejects_same_identity_for_both_roles():
    scraped = [{"category": "Cleaning", "fund": "admin", "actual": 17468.73}]
    stored = [{"name": "Cleaning", "actual_amount": 6678.73}]

    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=scraped, stored=stored)
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by="uuid-a")

    assert exit_code == 2
    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_apply_with_distinct_identities_calls_importer_with_only_changed_rows():
    scraped = [
        {"category": "Cleaning", "fund": "admin", "actual": 17468.73},
        {"category": "Audit Fees", "fund": "admin", "actual": 730.0},  # unchanged, must be excluded
    ]
    stored = [
        {"name": "Cleaning", "actual_amount": 6678.73, "fund_type": "admin"},
        {"name": "Audit Fees", "actual_amount": 730.0, "fund_type": "admin"},
    ]

    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=scraped, stored=stored)
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)
        mock_import.return_value = {"ok": True}

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by="uuid-b")

    assert exit_code == 0
    mock_import.assert_awaited_once()
    call = mock_import.call_args
    content = call.kwargs["content"].decode("utf-8")
    assert "Cleaning" in content
    assert "Audit Fees" not in content  # unchanged row must not be re-written
    assert call.kwargs["is_test_data"] is False


# ── Identity validation (2026-08-19 hardening): --authorised-by/--executed-by
# previously only had to be non-empty and different from each other — a typo'd or
# made-up UUID satisfied dual control. Now both must resolve to a real, active users
# doc with an eligible role. ─────────────────────────────────────────────────────────

_CHANGED_SCRAPED = [{"category": "Cleaning", "fund": "admin", "actual": 17468.73}]
_CHANGED_STORED = [{"name": "Cleaning", "actual_amount": 6678.73, "fund_type": "admin"}]


@pytest.mark.asyncio
async def test_apply_rejects_identity_that_is_not_a_real_user():
    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=_CHANGED_SCRAPED, stored=_CHANGED_STORED, users_by_id={"uuid-b": _VALID_EXECUTOR})
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-does-not-exist", executed_by="uuid-b")

    assert exit_code == 2
    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_apply_rejects_inactive_user():
    inactive_authoriser = {**_VALID_AUTHORISER, "is_active": False}
    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(
            scraped=_CHANGED_SCRAPED, stored=_CHANGED_STORED,
            users_by_id={"uuid-a": inactive_authoriser, "uuid-b": _VALID_EXECUTOR},
        )
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by="uuid-b")

    assert exit_code == 2
    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_apply_rejects_ineligible_role():
    """An owner account, for example, must not be able to satisfy dual control just by
    having a real, active users doc — the role must also be one of the eligible ones."""
    owner_executor = {**_VALID_EXECUTOR, "role": "owner"}
    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(
            scraped=_CHANGED_SCRAPED, stored=_CHANGED_STORED,
            users_by_id={"uuid-a": _VALID_AUTHORISER, "uuid-b": owner_executor},
        )
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by="uuid-b")

    assert exit_code == 2
    mock_import.assert_not_called()


@pytest.mark.asyncio
async def test_apply_reports_both_identity_problems_in_one_pass():
    with patch(
        "scripts.reconcile_scraped_financials_to_categories.motor.motor_asyncio.AsyncIOMotorClient"
    ) as mock_client_cls, patch(
        "scripts.reconcile_scraped_financials_to_categories.import_gap_tolerant_financial_data",
        new_callable=AsyncMock,
    ) as mock_import:
        mdb = _mock_mdb(scraped=_CHANGED_SCRAPED, stored=_CHANGED_STORED, users_by_id={})
        mock_client_cls.return_value.__getitem__ = MagicMock(return_value=mdb)

        exit_code = await run("13195", "2026", apply=True, authorised_by="uuid-a", executed_by="uuid-b")

    assert exit_code == 2
    mock_import.assert_not_called()
