# @featuretrace:cutover-toggle-safety — what actually blocks a route from serving Postgres.
# Layer: test
# Data flow: audit_route_wireability classification of backend/routers/finance.py (global).
# Related: scripts/validation/audit_route_wireability.py
#          backend/services/store_router.py
"""The blocker classification must stay true, because the plan is built on it.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_route_wireability.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validation" / "audit_route_wireability.py"

spec = importlib.util.spec_from_file_location("audit_route_wireability", SCRIPT)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_route_wireability"] = audit
spec.loader.exec_module(audit)

FINANCE = ROOT / "backend" / "routers" / "finance.py"


class TestClassification:
    def setup_method(self):
        self.handlers = {h: (m, r, d, c) for m, r, h, d, c in audit._handlers(FINANCE)}

    def test_the_wired_routes_are_detected_as_dispatched(self):
        for handler in ("get_expenses", "get_income_transactions", "get_available_years"):
            assert self.handlers[handler][2] is True, f"{handler} should read as dispatched"

    def test_an_unwired_route_is_not_detected_as_dispatched(self):
        assert self.handlers["get_levy_payments"][2] is False

    def test_every_known_blocker_names_a_reason_not_just_a_label(self):
        """The label is an index into the reason. A label alone is a verdict nobody can
        check, and the reason is what stops the analysis being redone by hand."""
        for handler, (klass, why) in audit.KNOWN_BLOCKERS.items():
            assert klass in {"needs_contract", "needs_design", "concept_mismatch",
                             "needs_upstream"}
            assert len(why) > 80, f"{handler}: reason is too thin to act on"

    def test_blockers_reference_handlers_that_still_exist(self):
        """A stale entry silently mis-classifies a route that has since been renamed."""
        for handler in audit.KNOWN_BLOCKERS:
            assert handler in self.handlers, (
                f"KNOWN_BLOCKERS names '{handler}', which no longer exists in finance.py"
            )


class TestTheFindingThatChangedThePlan:
    """Collection-level mapping said six routes were ready. Only one was.

    If someone reclassifies these as merely needing data, the plan silently reverts to
    "migrate the collection and it will work", which is the thing that was wrong.
    """

    def test_levy_payments_is_blocked_on_the_contract_not_the_data(self):
        klass, why = audit.KNOWN_BLOCKERS["get_levy_payments"]
        assert klass == "needs_contract"
        assert "status" in why and "receipts" in why

    def test_bank_reconciliations_is_a_concept_mismatch_not_missing_data(self):
        """finance.fund_bank_reconciliations has rows — and answers a different question."""
        klass, why = audit.KNOWN_BLOCKERS["get_bank_reconciliations"]
        assert klass == "concept_mismatch"
        assert "different question" in why

    def test_the_contact_log_is_a_nested_subdocument_with_no_relational_home(self):
        klass, why = audit.KNOWN_BLOCKERS["get_unit_contact_log"]
        assert klass == "concept_mismatch"
        assert "core.lots has no such column" in why

    def test_unit_levy_ledger_is_a_derivation_task_not_a_copy(self):
        klass, why = audit.KNOWN_BLOCKERS["get_unit_levy_ledger"]
        assert klass == "needs_design"
        assert "DERIVE" in why
        assert "allocation-trail" in why


class TestUpstreamBlocker:
    """levy_categories IS migrated. The route is still blocked, for a different reason.

    This distinction is the whole point of the class: a route whose own table is done
    and whose join is clean can still be unservable because a table it DERIVES from is
    short. Classifying it as needs_data would send someone to migrate a collection that
    is already migrated.
    """

    def test_levy_categories_is_blocked_upstream_not_on_its_own_data(self):
        klass, why = audit.KNOWN_BLOCKERS["get_levy_categories"]
        assert klass == "needs_upstream"
        assert "IS migrated" in why
        assert "expense_transactions" in why

    def test_the_blocker_quantifies_the_shortfall(self):
        """A blocker with a number is actionable; one without is an opinion."""
        _, why = audit.KNOWN_BLOCKERS["get_levy_categories"]
        assert "$145,652.65" in why and "$283,206.22" in why

    def test_the_budget_side_is_recorded_as_already_correct(self):
        """So nobody re-migrates the categories looking for the discrepancy."""
        _, why = audit.KNOWN_BLOCKERS["get_levy_categories_budget_summary"]
        assert "matches MongoDB exactly" in why
