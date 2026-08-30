# @featuretrace:cutover-toggle-safety — every populated Mongo collection has a decided fate.
# Layer: test
# Data flow: mongo_collection_disposition.yaml -> audit_collection_disposition (global).
# Related: docs/architecture/mongo_collection_disposition.yaml
#          scripts/validation/audit_collection_disposition.py
"""The disposition policy must stay internally consistent to be worth trusting.

Coverage against the live database is checked by `--check --live`, which needs MongoDB.
These tests need nothing and so can gate every PR.

Run:
    backend/venv/bin/python3 -m pytest tests/backend/test_collection_disposition.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validation" / "audit_collection_disposition.py"
POLICY = ROOT / "docs" / "architecture" / "mongo_collection_disposition.yaml"

spec = importlib.util.spec_from_file_location("audit_collection_disposition", SCRIPT)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_collection_disposition"] = audit
spec.loader.exec_module(audit)


class TestPolicyIsConsistent:
    def test_the_policy_has_no_internal_problems(self):
        _, problems = audit.load_policy()
        assert not problems, "policy problems:\n  " + "\n  ".join(problems)

    def test_no_collection_is_classified_twice(self):
        """Two decisions for one collection means neither is the decision."""
        classified, problems = audit.load_policy()
        assert not [p for p in problems if "twice" in p]
        assert len(classified) > 100, "policy looks truncated"

    def test_every_section_states_a_rationale(self):
        doc = yaml.safe_load(POLICY.read_text())
        for section in audit.SECTIONS:
            assert (doc[section].get("rationale") or "").strip(), (
                f"section '{section}' must say WHY, not just what — a classification "
                f"with no reasoning gets reversed by the next person"
            )


class TestTheDecisionsThatMatter:
    """These are the calls that reframed the work. If one is reversed, say so loudly."""

    def setup_method(self):
        self.classified, _ = audit.load_policy()

    def test_the_five_highest_volume_collections_stay_on_mongo(self):
        """They are 41,140 of 45,631 documents — 90% — and all append-only logs.

        This is the decision that turned an unsized backlog into ~4,500 documents of
        real work. Reversing it silently would put 90% of the volume back.
        """
        for name in ("workflow_runs", "audit_logs", "email_sent_log",
                     "event_log", "user_notifications"):
            assert self.classified.get(name) in ("stay_on_mongo", "stay_on_mongo_additions"), \
                f"{name} is a high-volume append-only log and should stay on MongoDB"

    def test_unit_levy_ledger_is_scheduled_for_migration(self):
        """638 docs, no PostgreSQL target, and half the finance UI reads it."""
        assert self.classified.get("unit_levy_ledger") in ("migrate", "migrate_additions")

    def test_quarantine_collections_are_retired_not_migrated(self):
        """Migrating a quarantine perpetuates it."""
        for name in ("unit_levy_ledger_quarantine", "levy_categories_quarantine",
                     "annual_levies_quarantine"):
            assert self.classified.get(name) in ("retire", "retire_additions")

    def test_the_finance_ledger_collections_are_marked_coexisting(self):
        """Both stores hold them; MongoDB is the DR position, not backlog."""
        for name in ("levy_payments", "users", "units", "user_units"):
            assert self.classified.get(name) == "coexisting"

    def test_undecided_is_used_rather_than_guessed(self):
        """A confident wrong classification is worse — it gets acted on."""
        undecided = [k for k, v in self.classified.items() if v == "undecided"]
        assert undecided, (
            "no collection is marked undecided, which would mean every one of 153 was "
            "confidently classified — implausible, and the wrong ones get acted on"
        )

    def test_documents_is_not_promoted_by_this_policy(self):
        """Operator decision 2026-08-30: documents stays MongoDB-served for now.

        Asserted against the PARSED yaml, not the raw text: a folded scalar wraps at the
        column width, so a raw-text search for a phrase that happens to straddle a line
        break fails while the policy says exactly what it should. The parsed value is
        what any consumer actually reads.
        """
        doc = yaml.safe_load(POLICY.read_text())
        whys = " ".join(
            phase.get("why", "") for phase in doc["migrate_additions"]["phases"]
        )
        assert "stays MongoDB-served by operator decision" in whys, (
            "the policy must record that documents is deliberately left on MongoDB, "
            "otherwise a later reader sees it listed under migrate and promotes it"
        )
