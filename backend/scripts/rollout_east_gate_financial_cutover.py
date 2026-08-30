#!/usr/bin/env python3
"""Apply the authoritative East Gate financial cutover in Postgres.

This script reverses the stale East Gate genesis journals, restates the
authoritative fund opening balances, normalizes the scheme away from the
legacy ``financial_core.read_from_postgres`` override, and enables the
canonical Postgres finance/trust cutover flags that are safe for the current
rollout.

It intentionally leaves ``external_api_finance_pg_enabled`` disabled because
the current External API levy summaries still require levy_runs / levy_items
that have not yet been migrated for East Gate.

Authoritative inputs (BSB, account number, fund opening balances, etc.) are
read from ``core.building_settings`` under the key
``financial.cutover.authoritative_inputs``. The script never holds those
values in source control or environment variables — they live per-building
in Postgres so the same playbook scales across every scheme.

Bootstrap workflow::

    # 1. Seed the authoritative inputs once per building (idempotent upsert):
    python scripts/rollout_east_gate_financial_cutover.py \\
        --seed-inputs path/to/east_gate_inputs.json

    # 2. Dry-run / inspect current state (default; no flags):
    python scripts/rollout_east_gate_financial_cutover.py

    # 3. Apply the cutover:
    python scripts/rollout_east_gate_financial_cutover.py --apply

See ``scripts/templates/cutover_authoritative_inputs.example.json`` for the
expected JSON shape.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.financial_core import get_financial_core_service
from services.financial_core.domain.entities import (
    EntryDirection,
    JournalEntry,
    JournalLine,
    ReverseEntryCommand,
    SchemeRef,
)
from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant
from services.financial_core.genesis import (
    compute_evidence_doc_hash,
    enable_canonical_cutover_overrides,
    ensure_cutover_actor_user,
    normalize_fund_type,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EAST_GATE_BUILDING_ID = "13195"

CUTOVER_INPUTS_SETTING_KEY = "financial.cutover.authoritative_inputs"
RESTATEMENT_KEY_PREFIX = "financial-cutover-restatement"
DEFAULT_BANK_PROVIDER = "mock"
DEFAULT_BANK_MODE = "mock"
REQUIRED_INPUT_KEYS = {"as_at_date", "account_name", "bsb", "account_number", "funds"}
REQUIRED_FUND_KEYS = {"fund_type", "opening_balance_cents"}


def _mask_bsb(value: str) -> str:
    """Generated function header.

    Function: _mask_bsb
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"***-{digits[-3:]}" if len(digits) >= 3 else "***"


def _mask_account_number(value: str) -> str:
    """Generated function header.

    Function: _mask_account_number
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"****{digits[-4:]}" if digits else "****"


def _validate_inputs(inputs: dict[str, Any]) -> None:
    """Generated function header.

    Function: _validate_inputs
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    missing = sorted(REQUIRED_INPUT_KEYS - set(inputs))
    if missing:
        raise RuntimeError(
            f"{CUTOVER_INPUTS_SETTING_KEY} is missing required keys: {missing}. "
            "Re-seed via --seed-inputs <path-to-json>."
        )
    funds = inputs.get("funds") or []
    if not isinstance(funds, list) or not funds:
        raise RuntimeError(f"{CUTOVER_INPUTS_SETTING_KEY}.funds must be a non-empty list.")
    for fund in funds:
        fund_missing = sorted(REQUIRED_FUND_KEYS - set(fund))
        if fund_missing:
            raise RuntimeError(
                f"{CUTOVER_INPUTS_SETTING_KEY}.funds entry missing keys: {fund_missing}."
            )


async def _load_authoritative_inputs(building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: _load_authoritative_inputs
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    inputs = await config_repo.get_building_setting(building_id, CUTOVER_INPUTS_SETTING_KEY)
    if not inputs:
        raise RuntimeError(
            f"No {CUTOVER_INPUTS_SETTING_KEY} found in core.building_settings for "
            f"building_id={building_id!r}. Seed it first with: "
            f"--seed-inputs <path-to-json>"
        )
    # JSONB values normally arrive as already-decoded dicts; tolerate a raw
    # JSON string in case a future driver returns the textual form.
    if isinstance(inputs, str):
        inputs = json.loads(inputs)
    if not isinstance(inputs, dict):
        raise RuntimeError(
            f"{CUTOVER_INPUTS_SETTING_KEY} must be a JSON object; got {type(inputs).__name__}."
        )
    _validate_inputs(inputs)
    return inputs


async def _seed_authoritative_inputs(building_id: str, inputs_path: Path) -> None:
    """Generated function header.

    Function: _seed_authoritative_inputs
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = json.loads(inputs_path.read_text())
    if not isinstance(raw, dict):
        raise RuntimeError(f"{inputs_path} must contain a JSON object.")
    _validate_inputs(raw)
    await config_repo.upsert_building_setting(
        building_id,
        CUTOVER_INPUTS_SETTING_KEY,
        raw,
    )
    print(
        json.dumps(
            {
                "status": "seeded",
                "building_id": building_id,
                "setting_key": CUTOVER_INPUTS_SETTING_KEY,
                "source": str(inputs_path),
            },
            indent=2,
        )
    )


def _restatement_entry_key(building_id: str, fund_type: str, as_at_date: str) -> str:
    """Generated function header.

    Function: _restatement_entry_key
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"{RESTATEMENT_KEY_PREFIX}:{building_id}:{fund_type}:{as_at_date}"


def _restatement_reversal_key(building_id: str, journal_entry_id: str) -> str:
    """Generated function header.

    Function: _restatement_reversal_key
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return f"{RESTATEMENT_KEY_PREFIX}-reversal:{building_id}:{journal_entry_id}"


def _target_opening_balances(inputs: dict[str, Any]) -> dict[str, int]:
    """Generated function header.

    Function: _target_opening_balances
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        normalize_fund_type(str(fund["fund_type"])): int(fund["opening_balance_cents"])
        for fund in inputs["funds"]
    }


async def _resolve_scheme_ref(building_id: str) -> SchemeRef:
    """Generated function header.

    Function: _resolve_scheme_ref
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await config_repo.resolve_scheme_context(building_id)
    if scheme is None:
        raise RuntimeError(f"No Postgres scheme found for building {building_id}.")
    return SchemeRef(
        tenant_id=UUID(str(scheme["tenant_id"])),
        scheme_id=UUID(str(scheme["scheme_id"])),
    )


async def _load_financial_entries(session, scheme_ref: SchemeRef) -> list[dict[str, str | None]]:
    """Generated function header.

    Function: _load_financial_entries
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT je.journal_entry_id::text AS journal_entry_id,
                   je.source_type,
                   COALESCE(je.fund_id, original.fund_id)::text AS fund_id,
                   f.fund_type::text AS fund_type,
                   je.reversal_of_id::text AS reversal_of_id,
                   je.idempotency_key
            FROM finance.journal_entries je
            LEFT JOIN finance.journal_entries original
              ON original.journal_entry_id = je.reversal_of_id
            JOIN finance.funds f
              ON f.fund_id = COALESCE(je.fund_id, original.fund_id)
            WHERE je.scheme_id = :scheme_id
            ORDER BY je.created_at
            """
        ),
        {"scheme_id": str(scheme_ref.scheme_id)},
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def _post_opening_balance_restatement_entry(
    session,
    *,
    scheme_ref: SchemeRef,
    fund_id: UUID,
    fund_type: str,
    opening_balance_cents: int,
    building_id: str,
    inputs: dict[str, Any],
) -> None:
    """Generated function header.

    Function: _post_opening_balance_restatement_entry
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    financial_core = get_financial_core_service(session)
    period_id = await financial_core._ledger.get_current_accounting_period(scheme_ref)
    bank_account_id = await financial_core._ledger.get_gl_account(scheme_ref, "1000")
    opening_equity_account_id = await financial_core._ledger.get_gl_account(scheme_ref, "3100")
    amount_cents = abs(opening_balance_cents)
    bank_direction = EntryDirection.DEBIT if opening_balance_cents > 0 else EntryDirection.CREDIT
    equity_direction = EntryDirection.CREDIT if opening_balance_cents > 0 else EntryDirection.DEBIT
    as_at_date = str(inputs["as_at_date"])
    evidence_payload = next(
        balance for balance in inputs["funds"]
        if normalize_fund_type(str(balance["fund_type"])) == fund_type
    )
    evidence_source = str(
        inputs.get("evidence_source")
        or f"authoritative_cutover:{building_id}:{as_at_date}"
    )
    entry = JournalEntry(
        scheme_ref=scheme_ref,
        period_id=period_id,
        source_type="adjustment",
        source_reference=str(fund_id),
        narration=f"Authoritative opening balance restatement for {fund_type} fund",
        effective_on=datetime.fromisoformat(as_at_date).date(),
        lines=[
            JournalLine(
                gl_account_id=bank_account_id,
                direction=bank_direction,
                amount_cents=amount_cents,
                gst_cents=0,
                narration="Opening balance restatement (assets)",
            ),
            JournalLine(
                gl_account_id=opening_equity_account_id,
                direction=equity_direction,
                amount_cents=amount_cents,
                gst_cents=0,
                narration="Opening balance restatement (equity)",
            ),
        ],
        fund_id=fund_id,
        idempotency_key=_restatement_entry_key(building_id, fund_type, as_at_date),
        metadata={
            "building_id": building_id,
            "restatement_kind": "authoritative_opening_balance",
            "evidence_doc_hash": compute_evidence_doc_hash(
                {
                    "fund_type": fund_type,
                    "opening_balance_cents": opening_balance_cents,
                    "as_at_date": as_at_date,
                    "account_name": str(inputs["account_name"]),
                    "bsb": str(inputs["bsb"]),
                    "account_number": str(inputs["account_number"]),
                    "evidence_source": evidence_source,
                    "notes": evidence_payload.get("annual_total_ex_gst"),
                }
            ),
        },
    )
    saved_entry = await financial_core._ledger.save_journal_entry(entry)
    await financial_core._ledger.post_journal_entry(saved_entry.journal_entry_id, scheme_ref)
    await financial_core._ledger.set_fund_opening_balance(
        fund_id,
        scheme_ref,
        opening_balance_cents,
    )


async def _restate_existing_genesis_if_present(
    session,
    scheme_ref: SchemeRef,
    building_id: str,
    inputs: dict[str, Any],
) -> None:
    """Generated function header.

    Function: _restate_existing_genesis_if_present
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    rows = await _load_financial_entries(session, scheme_ref)
    if not rows:
        return
    target_balances = _target_opening_balances(inputs)
    as_at_date = str(inputs["as_at_date"])
    allowed_source_types = {"genesis", "reversal", "adjustment"}
    unexpected_entries = [
        str(row["journal_entry_id"])
        for row in rows
        if str(row["source_type"]) not in allowed_source_types
    ]
    if unexpected_entries:
        raise RuntimeError(
            f"Building {building_id} has non-cutover journal entries in Postgres; "
            "refusing to restate opening balances. "
            f"Unexpected entries: {', '.join(unexpected_entries)}"
        )

    financial_core = get_financial_core_service(session)
    rows_by_fund: dict[str, list[dict[str, str | None]]] = {}
    for row in rows:
        rows_by_fund.setdefault(normalize_fund_type(str(row["fund_type"])), []).append(row)

    for fund_type, opening_balance_cents in target_balances.items():
        fund_rows = rows_by_fund.get(fund_type, [])
        if not fund_rows:
            continue
        fund_id = UUID(str(fund_rows[0]["fund_id"]))
        genesis_rows = [row for row in fund_rows if str(row["source_type"]) == "genesis"]
        if not genesis_rows:
            raise RuntimeError(
                f"Building {building_id} fund {fund_type} has no genesis entry to restate."
            )

        reversed_ids = {
            str(row["reversal_of_id"])
            for row in fund_rows
            if str(row["source_type"]) == "reversal" and row.get("reversal_of_id")
        }
        for row in genesis_rows:
            if str(row["journal_entry_id"]) in reversed_ids:
                continue
            await financial_core.reverse_entry(
                ReverseEntryCommand(
                    scheme_ref=scheme_ref,
                    journal_entry_id=UUID(str(row["journal_entry_id"])),
                    reason=f"Authoritative {building_id} opening balance restatement",
                    effective_on=datetime.fromisoformat(as_at_date).date(),
                    idempotency_key=_restatement_reversal_key(
                        building_id, str(row["journal_entry_id"])
                    ),
                )
            )

        expected_key = _restatement_entry_key(building_id, fund_type, as_at_date)
        restatement_present = any(
            str(row["source_type"]) == "adjustment"
            and str(row.get("idempotency_key") or "") == expected_key
            for row in fund_rows
        )
        if not restatement_present:
            await _post_opening_balance_restatement_entry(
                session,
                scheme_ref=scheme_ref,
                fund_id=fund_id,
                fund_type=fund_type,
                opening_balance_cents=opening_balance_cents,
                building_id=building_id,
                inputs=inputs,
            )


async def _update_trust_account_masks(
    session, scheme_ref: SchemeRef, inputs: dict[str, Any]
) -> None:
    """Generated function header.

    Function: _update_trust_account_masks
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            """
            UPDATE finance.trust_accounts
            SET account_name = :account_name,
                masked_bsb = :masked_bsb,
                masked_account_number = :masked_account_number
            WHERE scheme_id = :scheme_id
              AND COALESCE(status, 'active') = 'active'
            """
        ),
        {
            "scheme_id": str(scheme_ref.scheme_id),
            "account_name": str(inputs["account_name"]),
            "masked_bsb": _mask_bsb(str(inputs["bsb"])),
            "masked_account_number": _mask_account_number(str(inputs["account_number"])),
        },
    )


async def _stamp_cutover_inputs_audit(
    session,
    scheme_ref: SchemeRef,
    inputs: dict[str, Any],
    *,
    set_by_user_id: UUID,
) -> None:
    """Re-stamp the inputs row with the cutover actor / timestamp.

    The values themselves are seeded ahead of time via ``--seed-inputs``;
    this just records who applied the cutover and when.
    """
    await session.execute(
        text(
            """
            INSERT INTO core.building_settings
                (tenant_id, scheme_id, setting_key, setting_value, set_by, set_at)
            VALUES
                (:tenant_id, :scheme_id, :setting_key, CAST(:setting_value AS jsonb), :set_by, NOW())
            ON CONFLICT (scheme_id, setting_key)
            DO UPDATE
                SET setting_value = EXCLUDED.setting_value,
                    set_by = EXCLUDED.set_by,
                    set_at = NOW()
            """
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "setting_key": CUTOVER_INPUTS_SETTING_KEY,
            "setting_value": json.dumps(inputs),
            "set_by": str(set_by_user_id),
        },
    )


async def _print_current_state(building_id: str, label: str) -> None:
    """Generated function header.

    Function: _print_current_state
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme_ref = await _resolve_scheme_ref(building_id)
    async with async_session_context() as session:
        await set_tenant(session, str(scheme_ref.tenant_id))
        result = await session.execute(
            text(
                """
                SELECT feature_key, is_enabled, reason
                FROM core.feature_toggle_overrides
                WHERE scheme_id = :scheme_id
                ORDER BY feature_key
                """
            ),
            {"scheme_id": str(scheme_ref.scheme_id)},
        )
        overrides = [dict(row._mapping) for row in result.fetchall()]
        result = await session.execute(
            text(
                """
                SELECT f.fund_code, f.opening_balance_cents, je.effective_on, je.source_type
                FROM finance.funds f
                LEFT JOIN finance.journal_entries je
                  ON je.fund_id = f.fund_id
                 AND je.scheme_id = f.scheme_id
                WHERE f.scheme_id = :scheme_id
                ORDER BY f.fund_code, je.created_at
                """
            ),
            {"scheme_id": str(scheme_ref.scheme_id)},
        )
        funds = [dict(row._mapping) for row in result.fetchall()]
    print(json.dumps({"label": label, "overrides": overrides, "fund_rows": funds}, default=str, indent=2))


async def apply_cutover(building_id: str) -> None:
    """Generated function header.

    Function: apply_cutover
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    inputs = await _load_authoritative_inputs(building_id)
    scheme_ref = await _resolve_scheme_ref(building_id)
    as_at_date = str(inputs["as_at_date"])
    account_name = str(inputs["account_name"])
    bsb = str(inputs["bsb"])
    account_number = str(inputs["account_number"])
    evidence_source = str(
        inputs.get("evidence_source")
        or f"authoritative_cutover:{building_id}:{as_at_date}"
    )
    bank_provider = str(inputs.get("bank_provider") or DEFAULT_BANK_PROVIDER)
    bank_mode = str(inputs.get("bank_mode") or DEFAULT_BANK_MODE)

    opening_balances = [
        {
            "fund_type": str(fund["fund_type"]),
            "opening_balance_cents": int(fund["opening_balance_cents"]),
            "as_at_date": as_at_date,
            "account_name": account_name,
            "bsb": bsb,
            "account_number": account_number,
            "evidence_source": evidence_source,
            "notes": fund.get("notes")
            or (
                f"Annual {normalize_fund_type(str(fund['fund_type']))} total ex GST: "
                f"{fund.get('annual_total_ex_gst', '—')}"
            ),
        }
        for fund in inputs["funds"]
    ]

    async with async_session_context() as session:
        await set_tenant(session, str(scheme_ref.tenant_id))
        cutover_actor_user_id = await ensure_cutover_actor_user(
            session,
            scheme_ref=scheme_ref,
            building_id=building_id,
        )
        await _restate_existing_genesis_if_present(
            session, scheme_ref=scheme_ref, building_id=building_id, inputs=inputs
        )
        enabled_features = await enable_canonical_cutover_overrides(
            session,
            scheme_ref=scheme_ref,
            set_by_user_id=cutover_actor_user_id,
            building_id=building_id,
            reason=(
                f"Canonical {building_id} financial cutover enabled after "
                "authoritative opening-balance restatement"
            ),
            enable_external_api_finance=False,
            enable_shadow_reads=False,
            bank_provider=bank_provider,
            bank_mode=bank_mode,
        )
        await _update_trust_account_masks(session, scheme_ref, inputs)
        await _stamp_cutover_inputs_audit(
            session, scheme_ref, inputs, set_by_user_id=cutover_actor_user_id
        )
        cutover_record = {
            "canonical_cutover_enabled": True,
            "cutover_recorded_at": datetime.now(UTC).isoformat(),
            "building_id": building_id,
            "enabled_feature_keys": enabled_features,
            "banking_provider": bank_provider,
            "banking_mode": bank_mode,
            "funds": [
                {
                    "fund_type": normalize_fund_type(str(balance["fund_type"])),
                    "opening_balance_cents": int(balance["opening_balance_cents"]),
                    "as_at_date": str(balance["as_at_date"]),
                    "evidence_doc_hash": compute_evidence_doc_hash(balance),
                }
                for balance in opening_balances
            ],
        }
    print(json.dumps({"status": "applied", "cutover_record": cutover_record}, default=str, indent=2))


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/rollout_east_gate_financial_cutover.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Roll out the authoritative Postgres financial cutover for a building. "
            f"Defaults to East Gate ({EAST_GATE_BUILDING_ID}); override with --building-id "
            "to reuse the same playbook for any other scheme that has authoritative "
            "inputs seeded in core.building_settings."
        )
    )
    parser.add_argument(
        "--building-id",
        default=EAST_GATE_BUILDING_ID,
        metavar="ID",
        help=f"Target building_id (default: {EAST_GATE_BUILDING_ID}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the cutover. Without this flag the script prints the current state only.",
    )
    parser.add_argument(
        "--seed-inputs",
        type=Path,
        metavar="PATH",
        help=(
            "Upsert authoritative cutover inputs for the building from a JSON file "
            "into core.building_settings, then exit. Run once before --apply."
        ),
    )
    args = parser.parse_args()

    if args.seed_inputs is not None:
        if args.apply:
            parser.error("--seed-inputs and --apply are mutually exclusive.")
        asyncio.run(_seed_authoritative_inputs(args.building_id, args.seed_inputs))
        return

    if args.apply:
        asyncio.run(apply_cutover(args.building_id))
    else:
        asyncio.run(_print_current_state(args.building_id, "dry-run"))


if __name__ == "__main__":
    main()
