# @featuretrace:demo_bank — Shared read-path guard excluding archived demo_bank_transactions rows.
# Layer: repository
# Data flow: any "list/candidate/aggregate" read of demo_bank_transactions should route its
#            building_id (+ any other) filter through active_demo_bank_filter() so an archived
#            (superseded) row can never resurface in a live view once archived.
# Related: scripts/finances/archive_east_gate_pre_rebuild_data.py (the writer of is_archived)
#          services/reconstruction_batch_service.py, routers/finance_reconciliation.py
# Collection: demo_bank_transactions
"""Shared filter helper for demo_bank_transactions reads.

A single-writer update_many() can mark rows archived, but nothing forces every downstream read
to actually exclude them — this repo's own CLAUDE.md documents this exact class of bug repeatedly
(archived buildings resurfacing in listings, is_test_data leaking into real dashboards). Every
"list/candidate/aggregate" read of demo_bank_transactions should route its filter through
active_demo_bank_filter() rather than hand-rolling `{"is_archived": {"$ne": True}}` ad hoc, so a
future refactor only has one place to check.

Writes (update_one/update_many operating on an already-identified _id, or the archiving script
itself) do not need this guard — it exists specifically for reads that decide what to show/use.
"""
from __future__ import annotations

from typing import Any


def active_demo_bank_filter(building_id: str, **extra: Any) -> dict[str, Any]:
    """Mongo filter for demo_bank_transactions scoped to building_id, excluding archived rows.
    Pass any additional filter keys as kwargs (e.g. source_type=..., unit_number=...)."""
    return {"building_id": building_id, "is_archived": {"$ne": True}, **extra}
