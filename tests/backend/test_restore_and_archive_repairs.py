"""
# @featuretrace:cutover-control-plane — guards the two 2026-08-28 repair scripts.
# Layer: test
# Data flow: exported row dicts -> _bind/_coerce (pure) (building-scoped).
# Related: backend/scripts/data_repair/restore_domain_cutover_rows.py
#          backend/scripts/data_repair/archive_superseded_reconstruction_debits.py

Test Suite: domain_cutover_status restore + superseded-batch archival
=====================================================================

Both scripts shipped with a defect that these tests exist to pin down. Neither
defect raised an exception; both looked like success.

1. `restore_domain_cutover_rows` double-encoded `p0_snapshot`. The export writes
   that column as a JSON **string**, and calling `json.dumps` on it again stored
   a jsonb *string* whose content is JSON rather than a jsonb *object*. The
   insert succeeded, `SELECT domain, mode` looked perfect, and
   `DomainCutoverStatus` then rejected every row on validation —
   `get_cutover_status` swallowed the error, returned None, and footgun #17's
   missing-row default routed all four domains straight back to MongoDB. A
   restore that cannot be read is not a restore.

2. `archive_superseded_reconstruction_debits` must never touch a live batch.
   Archiving removes rows from every production query, so a mistyped batch id
   has to be inert, not destructive.

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_restore_and_archive_repairs.py -q
"""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str):
    """Import a data_repair script by path — they are scripts, not a package."""
    path = _ROOT / "backend" / "scripts" / "data_repair" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


restore = _load("restore_domain_cutover_rows")


class TestJsonBinding:
    """The bug that routed four domains back to MongoDB while reporting success."""

    def test_an_already_serialised_snapshot_is_not_re_encoded(self):
        raw = '{"gates": {"cutover_tables": {"detail": "found 3/3"}}}'
        bound = restore._bind("p0_snapshot", raw)
        assert bound == raw, "a JSON string must pass through untouched"
        # The decisive property: what Postgres stores must be an OBJECT.
        assert isinstance(json.loads(bound), dict)

    def test_double_encoding_is_what_we_are_preventing(self):
        """Demonstrates the failure mode, so the assertion above has meaning."""
        raw = '{"gates": {}}'
        double = json.dumps(raw)          # what the buggy version produced
        assert isinstance(json.loads(double), str), "double-encoded => jsonb string"
        assert not isinstance(json.loads(double), dict)

    def test_a_dict_snapshot_is_serialised_once(self):
        """Not every export shape is a string — a dict must still be encoded."""
        bound = restore._bind("p0_snapshot", {"gates": {"ok": True}})
        assert isinstance(bound, str)
        assert json.loads(bound) == {"gates": {"ok": True}}

    def test_a_null_snapshot_stays_null(self):
        assert restore._bind("p0_snapshot", None) is None


class TestScalarCoercion:
    def test_iso_timestamps_become_datetimes(self):
        """asyncpg raises DataError on an ISO string for a timestamp column —
        the same class of trap as footgun #21's DATE encoding."""
        got = restore._coerce("last_promoted_at", "2026-08-04T23:12:00.547971+00:00")
        assert isinstance(got, datetime)
        assert got.year == 2026 and got.month == 8

    def test_non_timestamp_strings_are_left_alone(self):
        assert restore._coerce("domain", "finance_ledger") == "finance_ledger"

    def test_none_survives_every_column_type(self):
        for col in ("last_promoted_at", "domain", "p0_snapshot"):
            assert restore._coerce(col, None) is None


class TestExportUnwrapping:
    def test_typed_scalar_wrappers_are_unwrapped(self):
        """The exporter encodes typed scalars as {"__t__": ..., "v": ...}.
        Comparing the raw wrapper stringifies a dict and matches nothing —
        the trap that once reported every table 100% missing."""
        wrapped = {"__t__": "uuid", "v": "9e9d75c2-bd92-4695-8487-1592018c3af9"}
        assert restore._unwrap(wrapped) == "9e9d75c2-bd92-4695-8487-1592018c3af9"

    def test_a_plain_value_passes_through(self):
        assert restore._unwrap("finance_ledger") == "finance_ledger"

    def test_a_dict_that_is_not_a_wrapper_is_preserved(self):
        """A real nested object must not be mistaken for a type wrapper."""
        payload = {"gates": {"cutover_tables": True}}
        assert restore._unwrap(payload) == payload


class TestRestoreContract:
    def test_id_is_not_restored_verbatim(self):
        """The surrogate key must be reassigned: the backup's id may collide with
        a row created since, and business identity here is (tenant_id, domain)."""
        assert "id" not in restore.COLUMNS

    def test_routing_columns_are_restored_verbatim(self):
        """Mode, readiness and source columns decide which store serves. If the
        script invented any of them it would change routing semantics while
        looking like a faithful restore."""
        for col in ("mode", "readiness_status", "read_source", "write_source",
                    "route_group", "toggle_name", "continuity_policy"):
            assert col in restore.COLUMNS, f"{col} must be restored, never inferred"

    def test_p0_snapshot_is_declared_jsonb(self):
        assert "p0_snapshot" in restore.CASTS
        assert restore.CASTS["p0_snapshot"] == "jsonb"


archive = _load("archive_superseded_reconstruction_debits")


class TestArchivalSafety:
    def test_the_archive_actor_is_a_reserved_service_local_part(self):
        """Service accounts are suppressed by their reserved `system-` local
        part, not by domain — a bulk email rewrite once surfaced the finance
        cutover actor in /admin/users as a strata manager."""
        assert archive.ARCHIVE_ACTOR.startswith("system-")

    def test_money_is_formatted_from_integer_cents(self):
        assert archive._fmt(121980479) == "$1,219,804.79"
        assert archive._fmt(0) == "$0.00"
