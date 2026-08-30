# @featuretrace:scheduler — GenerateLeviesWorkflow: durable Temporal saga for quarterly levy generation.
# Layer: worker
# Data flow: Temporal worker → GenerateLeviesWorkflow → compute_lot_levies / allocate_crns /
#             create_levy_documents / send_levy_notices → unit_levy_ledger (building-scoped).
# Related: backend/workers/temporal_worker.py (registers this workflow)
#           backend/workers/arq_settings.py (ARQ routine jobs — parallel tier)
#           backend/repositories/financial_repository.py (rebuild_projection reads unit_levy_ledger)
"""
NOT DEPLOYED (verified live 2026-08-11) — see backend/workers/temporal_worker.py's module
docstring. No Temporal server/worker runs anywhere in this deployment; this workflow has never
executed in production.

GenerateLeviesWorkflow — durable multi-step levy generation saga.

Workflow ID: `levygen:{building_id}:{quarter}` (e.g. levygen:13195:2026-Q2)
Temporal prevents duplicate workflow IDs, so the workflow is inherently
idempotent — a second start with the same ID returns the existing run.

Activities run in sequence; each is retried automatically by Temporal on
transient failure. The workflow resumes from the last completed activity
after any process crash.

Production deployment: connect temporal_worker.py to Temporal Cloud or
self-hosted cluster via TEMPORAL_HOST env var. See
docs/architecture/temporal-production-path.md for options.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

logger = logging.getLogger(__name__)

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)


# ── Activity input/output dataclasses ─────────────────────────────────────────

@dataclass
class LevyGenerationInput:
    building_id: str
    quarter: str  # e.g. "2026-Q2"
    budget_year: str  # e.g. "2026"
    is_test_data: bool = False


@dataclass
class LotLevy:
    lot_id: str
    lot_number: str
    owner_name: str
    levy_cents: int
    crn: Optional[str] = None


# ── Activities ────────────────────────────────────────────────────────────────

@activity.defn
async def compute_lot_levies(inp: LevyGenerationInput) -> List[Dict[str, Any]]:
    """Compute the levy amount for every lot in the building for the quarter.

    Reads: annual_levies, units, unit_entitlements.
    Returns a list of {lot_id, lot_number, owner_name, levy_cents}.
    """
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(inp.building_id)

    levy_doc = await db.annual_levies.find_one(
        {"building_id": inp.building_id, "year": inp.budget_year}
    )
    if not levy_doc:
        raise ValueError(f"No annual levy document for building {inp.building_id} year {inp.budget_year}")

    from utils.finance_helpers import get_levy_proposed_amounts
    annual_amounts = get_levy_proposed_amounts(levy_doc)
    quarterly_admin = (annual_amounts.get("admin_fund", 0) or 0) // 4
    quarterly_sinking = (annual_amounts.get("sinking_fund", 0) or 0) // 4

    units = await db.units.find({"building_id": inp.building_id}).to_list(5000)
    results = []
    for unit in units:
        entitlement = unit.get("unit_entitlement", 1) or 1
        total_entitlement = levy_doc.get("total_unit_entitlement", 1) or 1
        share = entitlement / total_entitlement
        levy_cents = round((quarterly_admin + quarterly_sinking) * share)
        results.append({
            "lot_id": str(unit.get("_id", "")),
            "lot_number": unit.get("unit_number", ""),
            "owner_name": unit.get("owner_name", "Unknown"),
            "levy_cents": levy_cents,
        })

    activity.logger.info("compute_lot_levies: %d lots for %s %s", len(results), inp.building_id, inp.quarter)
    return results


@activity.defn
async def allocate_crns(
        building_id: str,
        lot_levies: List[Dict[str, Any]],
        quarter: str,
) -> List[Dict[str, Any]]:
    """Allocate a Customer Reference Number for each lot levy via BillerProvider.

    Idempotent: if a CRN was already allocated for this lot+quarter, returns it.
    Falls back to a deterministic synthetic CRN if no biller is configured.
    """
    from integrations.registry import get_provider_registry

    registry = get_provider_registry()
    try:
        biller = await registry.get_biller(building_id)
    except Exception:
        biller = None

    enriched = []
    for levy in lot_levies:
        crn: Optional[str] = None
        if biller is not None:
            try:
                crn = await biller.allocate_reference(
                    building_id=building_id,
                    lot_id=levy["lot_id"],
                    reference_tag=f"{quarter}:{levy['lot_number']}",
                )
            except Exception as exc:
                activity.logger.warning("CRN allocation failed for lot %s: %s", levy["lot_id"], exc)

        if crn is None:
            # Synthetic fallback: building_id prefix + lot_number + quarter hash
            import hashlib
            tag = f"{building_id}:{levy['lot_number']}:{quarter}"
            crn = "SYN-" + hashlib.sha256(tag.encode()).hexdigest()[:12].upper()

        enriched.append({**levy, "crn": crn})

    return enriched


@activity.defn
async def create_levy_documents(
        inp: LevyGenerationInput,
        lot_levies: List[Dict[str, Any]],
) -> List[str]:
    """Persist levy notice documents in unit_levy_ledger.

    Returns a list of newly inserted ledger document IDs.
    Idempotent: upserts on (building_id, lot_id, quarter) — re-running
    creates no duplicates in unit_levy_ledger.
    """
    from database import db
    from request_context import set_ctx_building_id
    from datetime import datetime, timezone
    from pymongo import UpdateOne

    set_ctx_building_id(inp.building_id)
    now = datetime.now(timezone.utc)

    ops = []
    for levy in lot_levies:
        doc = {
            "building_id": inp.building_id,
            "lot_id": levy["lot_id"],
            "lot_number": levy["lot_number"],
            "owner_name": levy["owner_name"],
            "quarter": inp.quarter,
            "budget_year": inp.budget_year,
            "levy_cents": levy["levy_cents"],
            "crn": levy.get("crn"),
            "status": "issued",
            "created_at": now,
            "is_test_data": inp.is_test_data,
        }
        ops.append(UpdateOne(
            {
                "building_id": inp.building_id,
                "lot_id": levy["lot_id"],
                "quarter": inp.quarter,
            },
            {"$setOnInsert": doc},
            upsert=True,
        ))

    if not ops:
        return []

    result = await db._db.unit_levy_ledger.bulk_write(ops, ordered=False)
    inserted_ids = [str(uid) for uid in result.upserted_ids.values()]

    activity.logger.info(
        "create_levy_documents: %d new levy docs for %s %s",
        len(inserted_ids), inp.building_id, inp.quarter,
    )
    return inserted_ids


@activity.defn
async def send_levy_notices(
        building_id: str,
        lot_levies: List[Dict[str, Any]],
        quarter: str,
        is_test_data: bool = False,
) -> Dict[str, Any]:
    """Email levy notices to lot owners.

    Resolves owner emails from the users collection (canonical source) since
    compute_lot_levies does not populate owner_email. Falls back to
    levy["owner_email"] if already present (e.g. from upstream enrichment).
    In test mode (is_test_data=True), emails go to TEST_EMAIL_INTERCEPT_ADDRESS.
    """
    import os
    from utils.email import send_email_async
    from database import db
    from request_context import set_ctx_building_id

    set_ctx_building_id(building_id)

    # Batch-fetch owner emails keyed by unit_number (users is a global collection)
    owner_docs = await db._db.users.find(
        {"role": "owner", "is_approved": True, "building_id": building_id},
        {"unit_number": 1, "email": 1},
    ).to_list(5000)
    email_by_lot = {
        d["unit_number"]: d["email"]
        for d in owner_docs
        if d.get("unit_number") and d.get("email")
    }

    sent = 0
    failed = 0
    for levy in lot_levies:
        owner_email = levy.get("owner_email") or email_by_lot.get(levy.get("lot_number", ""))
        if not owner_email:
            continue
        if is_test_data:
            owner_email = os.getenv("TEST_EMAIL_INTERCEPT_ADDRESS", owner_email)
        try:
            await send_email_async(
                to_email=owner_email,
                subject=f"Levy Notice — {quarter}",
                body=(
                    f"Dear {levy.get('owner_name', 'Owner')},\n\n"
                    f"Your levy for {quarter} is ${levy['levy_cents'] / 100:.2f}.\n"
                    f"CRN: {levy.get('crn', 'N/A')}\n"
                ),
            )
            sent += 1
        except Exception as exc:
            activity.logger.warning("send_levy_notices: email failed for lot %s: %s", levy.get("lot_id"), exc)
            failed += 1

    return {"sent": sent, "failed": failed}


# ── Workflow ──────────────────────────────────────────────────────────────────

@workflow.defn
class GenerateLeviesWorkflow:
    """Durable saga for quarterly levy generation.

    Workflow ID convention: `levygen:{building_id}:{quarter}`
    Temporal ensures only one execution per workflow ID can run at a time,
    providing natural idempotency.

    Steps (each retried automatically by Temporal on failure):
      1. compute_lot_levies        — calculate amount per lot from budget
      2. allocate_crns             — assign CRN per lot via BillerProvider
      3. create_levy_documents     — persist to unit_levy_ledger (upsert)
      4. send_levy_notices         — email owners
    """

    @workflow.run
    async def run(self, inp: LevyGenerationInput) -> Dict[str, Any]:
        """Generated function header.

        Function: GenerateLeviesWorkflow.run
        Path: backend/workflows/generate_levies_workflow.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        workflow.logger.info(
            "GenerateLeviesWorkflow: %s %s (test=%s)",
            inp.building_id, inp.quarter, inp.is_test_data,
        )

        # Step 1: Compute levy amounts
        lot_levies = await workflow.execute_activity(
            compute_lot_levies,
            inp,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )

        # Step 2: Allocate CRNs
        lot_levies = await workflow.execute_activity(
            allocate_crns,
            args=[inp.building_id, lot_levies, inp.quarter],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )

        # Step 3: Persist levy documents
        inserted_ids = await workflow.execute_activity(
            create_levy_documents,
            args=[inp, lot_levies],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=_RETRY,
        )

        # Step 4: Email owners
        email_result = await workflow.execute_activity(
            send_levy_notices,
            args=[inp.building_id, lot_levies, inp.quarter, inp.is_test_data],
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        return {
            "building_id": inp.building_id,
            "quarter": inp.quarter,
            "lots_processed": len(lot_levies),
            "documents_created": len(inserted_ids),
            "emails_sent": email_result.get("sent", 0),
            "emails_failed": email_result.get("failed", 0),
        }
