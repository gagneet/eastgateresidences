"""
tests/backend/test_financial_core_models_mapper_configuration.py

Regression coverage for a 2026-07-24 finding: PgJournalEntry.evidence_document_id
declares ForeignKey("finance.evidence_documents.document_id"), but no SQLAlchemy
model for finance.evidence_documents existed anywhere in the codebase — every
prior reference to that table name was a raw-SQL script, never an ORM class.

SQLAlchemy resolves ALL registered foreign keys the first time any ORM object
from this Base is used in a process (configure_mappers()), so this broke every
call to FinancialCoreService.record_payment() — the sole authorised ledger-write
path per financial_matching.py's own comment — with a "could not find table to
generate a foreign key" error, process-wide, not building- or row-specific.
Found live while individually reviewing East Gate 13195's reconstruction queue.

Fixed by adding PgEvidenceDocument, matching the real finance.evidence_documents
schema (migrations 0044, 0051, 0068).
"""
from __future__ import annotations

import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def test_mapper_configuration_succeeds_for_every_financial_core_model():
    """The core regression assertion: SQLAlchemy's configure_mappers() must
    complete without raising for the whole financial_core Base — this is
    exactly the step that failed with 'could not find table finance.
    evidence_documents to generate a foreign key' before PgEvidenceDocument
    was added. Importing the models module alone isn't enough to trigger
    mapper configuration; it has to be forced explicitly."""
    from sqlalchemy.orm import configure_mappers

    import services.financial_core.adapters.db_postgres.models  # noqa: F401

    configure_mappers()


def test_pg_evidence_document_model_matches_the_real_table():
    from services.financial_core.adapters.db_postgres.models import PgEvidenceDocument

    assert PgEvidenceDocument.__tablename__ == "evidence_documents"
    assert PgEvidenceDocument.__table_args__ == {"schema": "finance"}
    columns = {c.name for c in PgEvidenceDocument.__table__.columns}
    # Spot-check columns from each of the three migrations that shaped this
    # table (0044 create, 0051 approval metadata, 0068 just extends a CHECK
    # constraint with no new columns) — not an exhaustive list.
    assert {"document_id", "tenant_id", "scheme_id", "building_id", "document_type",
            "file_url", "sha256_hash", "declared_total_cents"} <= columns
    assert {"approved_by", "approved_at", "notes", "supersedes_document_id"} <= columns


def test_pg_journal_entry_evidence_document_fk_resolves_to_pg_evidence_document():
    from services.financial_core.adapters.db_postgres.models import PgJournalEntry

    fk_targets = {
        fk.target_fullname for col in PgJournalEntry.__table__.columns for fk in col.foreign_keys
    }
    assert "finance.evidence_documents.document_id" in fk_targets
