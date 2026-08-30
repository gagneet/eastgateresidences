from datetime import date
import importlib.util
from pathlib import Path
import sys

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "scripts"
    / "migrations"
    / "migration_027_randomize_east_gate_demo_bank_levies.py"
)
_SPEC = importlib.util.spec_from_file_location("migration_027_randomize_east_gate_demo_bank_levies", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
migration_027 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = migration_027
_SPEC.loader.exec_module(migration_027)

PgParityStats = migration_027.PgParityStats
QUARTERS = migration_027.QUARTERS
UnitShare = migration_027.UnitShare
_allocate_cents = migration_027._allocate_cents
_description = migration_027._description
_guard_or_repair_pg_parity = migration_027._guard_or_repair_pg_parity
_payment_pattern = migration_027._payment_pattern
_split_uuid_refs = migration_027._split_uuid_refs


def test_allocate_cents_preserves_exact_total_with_residuals():
    weighted_keys = [
        (("U1", "Q1"), 1.0),
        (("U2", "Q1"), 1.0),
        (("U3", "Q1"), 1.0),
    ]

    allocation = _allocate_cents(100, weighted_keys)

    assert sum(allocation.values()) == 100
    assert sorted(allocation.values()) == [33, 33, 34]


def test_description_uses_actual_unit_count_not_east_gate_constant():
    description = _description(
        pattern="quarterly_regular",
        levy_year=2026,
        quarter="Q1",
        label="Admin Fund",
        unit_number="A101",
        amount_inc_cents=12345,
        amount_ex_cents=11223,
        gst_cents=1122,
        paid_date=date(2026, 3, 31),
        due_date=date(2026, 3, 31),
        lag_days=0,
        basis="proposed_admin_expenses",
        annual_ex_gst_cents=1000000,
        annual_inc_gst_cents=1100000,
        gst_rate=0.1,
        unit_count=42,
    )

    assert "across 42 units and 4 quarters" in description
    assert "across 87 units" not in description


def test_split_uuid_refs_keeps_valid_refs_and_marks_invalid_doc_ids():
    docs = [
        {"_id": "ok", "finance_bank_transaction_ref": "11111111-1111-1111-1111-111111111111"},
        {"_id": "bad", "finance_bank_transaction_ref": "not-a-uuid"},
        {"_id": "blank", "finance_bank_transaction_ref": ""},
    ]

    valid_refs, invalid_ids = _split_uuid_refs(docs)

    assert valid_refs == ["11111111-1111-1111-1111-111111111111"]
    assert invalid_ids == ["bad", "blank"]


def test_payment_patterns_are_counted_per_unit_year_not_per_fund():
    units = [UnitShare(unit_number="U1", uoe=1.0), UnitShare(unit_number="U2", uoe=1.0)]
    pattern_counts: dict[str, int] = {}

    for year in (2025, 2026):
        unit_patterns = {unit.unit_number: _payment_pattern("B-1", unit.unit_number, year) for unit in units}
        for unit in units:
            pattern = unit_patterns[unit.unit_number]
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        for _fund in ("admin", "sinking"):
            for unit in units:
                for _quarter in QUARTERS:
                    assert unit_patterns[unit.unit_number]

    assert sum(pattern_counts.values()) == len(units) * 2


async def test_invalid_synced_pg_ref_resets_to_pending_when_repair_enabled():
    payload = {"sync_status": "synced", "finance_bank_transaction_ref": "not-a-uuid"}
    stats = PgParityStats()

    should_update = await _guard_or_repair_pg_parity(
        None,
        {"sync_status": "synced", "finance_bank_transaction_ref": "not-a-uuid"},
        payload,
        repair_stale_sync=True,
        dry_run=False,
        parity_stats=stats,
    )

    assert should_update is True
    assert payload["sync_status"] == "pending"
    assert "finance_bank_transaction_ref" not in payload
    assert stats.invalid_refs == 1
    assert stats.reset_to_pending == 1
