"""M3 — Pre-migration data audit.

Read-only inspection of live MongoDB data before writing any migration script.
Outputs a plain-text report to stdout; exit code 0 = audit ran, non-zero = error.

Checks:
  1. Amount field naming in financial collections
     (Postgres finance.* uses BIGINT cents; Mongo may use float dollars named 'amount')
  2. Enum audit — 'chairman' role values still present in live documents
  3. AGM sub-document structure — are agm_motions/agm_votes embedded or separate?
  4. Feature toggle role values — any toggle still carrying 'chairman'/'treasurer'
  5. Volume sizing — record counts for all P0-P4 migration target collections

Usage:
    cd backend && python3 scripts/pre_migration_audit.py
    cd backend && python3 scripts/pre_migration_audit.py --building 13195
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

# Default to East Gate production building
DEFAULT_BUILDING = "13195"

SEPARATOR = "─" * 70


def _h(title: str) -> None:
    """Generated function header.

    Function: _h
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def _ok(msg: str) -> None:
    """Generated function header.

    Function: _ok
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"  ✓  {msg}")


def _warn(msg: str) -> None:
    """Generated function header.

    Function: _warn
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"  ⚠  {msg}")


def _info(msg: str) -> None:
    """Generated function header.

    Function: _info
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"     {msg}")


# ---------------------------------------------------------------------------
# Check 1 — Amount field naming in financial collections
# ---------------------------------------------------------------------------

_FINANCIAL_COLLECTIONS = [
    "levy_payments",
    "financial_transactions",
    "trust_transactions_v2",
    "trust_ledger_entries",
    "expense_transactions",
    "bank_transactions",
]

# Fields that indicate float-dollar amounts (need ×100 conversion to cents)
_DOLLAR_FIELD_NAMES = {"amount", "paid_amount", "total", "value", "balance"}
# Fields that are already cents or clearly not amounts
_CENTS_FIELD_NAMES = {"amount_cents", "principal_cents", "balance_cents"}


async def check_amount_fields(building_id: str) -> None:
    """Generated function header.

    Function: check_amount_fields
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _h("CHECK 1 — Amount field naming in financial collections")
    print("  Postgres finance.* requires BIGINT cents. Any 'amount' float field")
    print("  needs ×100 conversion in the migration script.\n")

    for coll_name in _FINANCIAL_COLLECTIONS:
        coll = db[coll_name]
        count = await coll.count_documents({"building_id": building_id})
        if count == 0:
            _info(f"{coll_name}: 0 records — skipping field scan")
            continue

        # Sample up to 20 docs to discover field names
        sample = await coll.find(
            {"building_id": building_id},
            {"_id": 0},
        ).limit(20).to_list(None)

        all_keys: set[str] = set()
        for doc in sample:
            all_keys.update(doc.keys())

        dollar_fields = all_keys & _DOLLAR_FIELD_NAMES
        cents_fields = all_keys & _CENTS_FIELD_NAMES

        if dollar_fields:
            # Inspect whether these are actually floats
            first = sample[0]
            float_confirmed = []
            for f in dollar_fields:
                val = first.get(f)
                if isinstance(val, float):
                    float_confirmed.append(f"{f}={val}")
            if float_confirmed:
                _warn(
                    f"{coll_name} ({count} docs): FLOAT dollars detected — "
                    f"{', '.join(float_confirmed)} → multiply by 100 in migration"
                )
            else:
                _info(
                    f"{coll_name} ({count} docs): dollar-named fields present "
                    f"({', '.join(dollar_fields)}) but first sample not float — verify manually"
                )
        elif cents_fields:
            _ok(f"{coll_name} ({count} docs): already uses cents fields ({', '.join(cents_fields)})")
        else:
            _info(f"{coll_name} ({count} docs): no amount-named fields found — keys: {sorted(all_keys)[:8]}")


# ---------------------------------------------------------------------------
# Check 2 — Enum audit: 'chairman' role values in live documents
# ---------------------------------------------------------------------------

async def check_chairman_enum(building_id: str) -> None:
    """Generated function header.

    Function: check_chairman_enum
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _h("CHECK 2 — 'chairman' role enum values in live MongoDB")
    print("  Migration 0025 removed chairman as a top-level UserRole.")
    print("  EC chairs must be role=ec_member + ec_position=CHAIRMAN.\n")

    # users.role
    count = await db.users.count_documents({"building_id": building_id, "role": "chairman"})
    if count:
        _warn(f"users.role='chairman': {count} document(s) — must update to role='ec_member' before migration")
        docs = await db.users.find(
            {"building_id": building_id, "role": "chairman"},
            {"full_name": 1, "email": 1, "role": 1, "ec_position": 1},
        ).to_list(10)
        for d in docs:
            _info(f"  {d.get('email', '?')} — {d.get('full_name', '?')} (ec_position={d.get('ec_position', 'NOT SET')})")
    else:
        _ok("users.role: no 'chairman' values found")

    # ec_members.role
    count = await db.ec_members.count_documents({"building_id": building_id, "role": "chairman"})
    if count:
        _warn(f"ec_members.role='chairman': {count} document(s)")
        docs = await db.ec_members.find(
            {"building_id": building_id, "role": "chairman"},
            {"name": 1, "email": 1, "role": 1, "position": 1},
        ).to_list(10)
        for d in docs:
            _info(f"  {d.get('email', '?')} pos={d.get('position', '?')}")
    else:
        _ok("ec_members.role: no 'chairman' values found")

    # memberships.role
    count = await db.memberships.count_documents({"building_id": building_id, "role": "chairman"})
    if count:
        _warn(f"memberships.role='chairman': {count} document(s) — update to 'ec_member'")
    else:
        _ok("memberships.role: no 'chairman' values found")

    # feature_toggles.allowed_roles / roles containing 'chairman'
    for field in ("allowed_roles", "roles"):
        count = await db.feature_toggles.count_documents(
            {"$or": [{"building_id": building_id}, {"building_id": None}], field: "chairman"}
        )
        if count:
            _warn(f"feature_toggles.{field} still contains 'chairman': {count} document(s) — re-run seeds/feature_toggles.py")
        else:
            _ok(f"feature_toggles.{field}: no 'chairman' entries")

    # feature_toggles 'treasurer'
    count = await db.feature_toggles.count_documents(
        {"$or": [{"building_id": building_id}, {"building_id": None}], "roles": "treasurer"}
    )
    if count:
        _warn(f"feature_toggles.roles still contains 'treasurer': {count} document(s) — re-run seeds/feature_toggles.py")
    else:
        _ok("feature_toggles.roles: no 'treasurer' entries")


# ---------------------------------------------------------------------------
# Check 3 — AGM sub-document structure
# ---------------------------------------------------------------------------

async def check_agm_structure(building_id: str) -> None:
    """Generated function header.

    Function: check_agm_structure
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _h("CHECK 3 — AGM sub-document structure")
    print("  Determining whether agm_motions/agm_votes are embedded arrays or")
    print("  separate collections. Affects governance migration strategy.\n")

    agm_count = await db.agm.count_documents({"building_id": building_id})
    _info(f"agm collection: {agm_count} document(s)")

    if agm_count == 0:
        _ok("No AGM records — nothing to inspect")
        return

    agm_doc = await db.agm.find_one({"building_id": building_id})
    agm_keys = set(agm_doc.keys()) if agm_doc else set()
    _info(f"agm document top-level keys: {sorted(agm_keys)}")

    # Embedded arrays?
    for embed_key in ("motions", "agm_motions", "votes", "agm_votes", "agenda_items", "attendance"):
        val = agm_doc.get(embed_key) if agm_doc else None
        if val is not None:
            count = len(val) if isinstance(val, list) else "non-list"
            _warn(f"Embedded '{embed_key}' array found: {count} item(s) — migration must extract to separate rows")
        else:
            _info(f"No embedded '{embed_key}'")

    # Separate collections?
    for coll_name in ("agm_motions", "agm_votes", "agm_attendance"):
        count = await db[coll_name].count_documents({"building_id": building_id})
        if count:
            _ok(f"{coll_name}: {count} separate document(s) — straightforward row-level migration")
        else:
            _info(f"{coll_name}: 0 records (may be embedded or not yet created)")

    # Attendance
    att_count = await db.agm_attendance.count_documents({"building_id": building_id})
    _info(f"agm_attendance: {att_count} record(s)")


# ---------------------------------------------------------------------------
# Check 4 — UPDEMO5 override is_enabled field
# ---------------------------------------------------------------------------

async def check_demo_overrides() -> None:
    """Generated function header.

    Function: check_demo_overrides
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _h("CHECK 4 — UPDEMO5 feature toggle override field names")
    print("  Override docs must use 'is_enabled' (not 'enabled').\n")

    overrides = await db.feature_toggles.find(
        {"building_id": "UPDEMO5"},
        {"feature_key": 1, "is_enabled": 1, "enabled": 1},
    ).to_list(None)

    stale = [d for d in overrides if "enabled" in d and "is_enabled" not in d]
    if stale:
        _warn(f"{len(stale)} override doc(s) use 'enabled' instead of 'is_enabled':")
        for d in stale[:10]:
            _info(f"  feature_key={d.get('feature_key')}")
        _info("Fix: re-run seeds/demo_customer.py")
    elif overrides:
        _ok(f"{len(overrides)} override doc(s) all use 'is_enabled' ✓")
    else:
        _info("No UPDEMO5 override documents found (seed not yet run)")


# ---------------------------------------------------------------------------
# Check 5 — Volume sizing for all migration target collections
# ---------------------------------------------------------------------------

_MIGRATION_COLLECTIONS = {
    # P0 Financial
    "annual_levies":           "P0-fin",
    "unit_levy_ledger":        "P0-fin",
    "levy_payments":           "P0-fin",
    "trust_accounts_v2":       "P0-fin",
    "trust_transactions_v2":   "P0-fin",
    "bank_transactions":       "P0-fin",
    "bank_reconciliations":    "P0-fin",
    "trust_ledger_entries":    "P0-fin",
    # P1 Governance
    "agm":                     "P1-gov",
    "agm_attendance":          "P1-gov",
    "ec_members":              "P1-gov",
    "decisions":               "P1-gov",
    "by_laws":                 "P1-gov",
    "by_laws_acknowledgments": "P1-gov",
    # P2 Compliance
    "insurance_policies":      "P2-compliance",
    "data_breach_log":         "P2-compliance",
    "whs_incidents":           "P2-compliance",
    "whs_inductions":          "P2-compliance",
    "whs_swms":                "P2-compliance",
    "privacy_consents":        "P2-compliance",
    "compliance_registers":    "P2-compliance",
    # P3 Communications
    "announcements":           "P3-comms",
    "notices":                 "P3-comms",
    "login_audit_logs":        "P3-comms",
    # P4 Operations
    "maintenance_requests":    "P4-ops",
    "work_orders":             "P4-ops",
    "contractors":             "P4-ops",
    # P5 Ownership
    "strata_owners":           "P5-ownership",
    "user_units":              "P5-ownership",
}


async def check_volumes(building_id: str) -> None:
    """Generated function header.

    Function: check_volumes
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _h("CHECK 5 — Collection volumes for migration target collections")
    print(f"  Building: {building_id}\n")

    group_totals: dict[str, int] = {}
    current_group = ""

    for coll_name, group in _MIGRATION_COLLECTIONS.items():
        if group != current_group:
            current_group = group
            print(f"\n  [{group}]")

        count = await db[coll_name].count_documents({"building_id": building_id})
        if count == 0:
            _info(f"{coll_name}: 0 records")
        else:
            _ok(f"{coll_name}: {count:,} records")
        group_totals[group] = group_totals.get(group, 0) + count

    print("\n  ── Group totals ──")
    total = 0
    for group, cnt in group_totals.items():
        _info(f"[{group}]: {cnt:,} records")
        total += cnt
    _info(f"Grand total: {total:,} records to migrate")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(building_id: str) -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/pre_migration_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"\n{'═' * 70}")
    print(f"  StrataOS Pre-Migration Audit — building_id={building_id}")
    print(f"{'═' * 70}")

    await check_amount_fields(building_id)
    await check_chairman_enum(building_id)
    await check_agm_structure(building_id)
    await check_demo_overrides()
    await check_volumes(building_id)

    print(f"\n{'═' * 70}")
    print("  Audit complete. Review ⚠ warnings before writing migration scripts.")
    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    bid = DEFAULT_BUILDING
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--building" and i + 1 < len(sys.argv) - 1:
            bid = sys.argv[i + 2]
        elif not arg.startswith("--"):
            bid = arg

    client_ref = client
    try:
        asyncio.run(main(bid))
    finally:
        client_ref.close()
