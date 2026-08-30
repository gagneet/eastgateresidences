# @featuretrace:scheduler — Temporal worker process: registers GenerateLeviesWorkflow and ReconciliationCloseWorkflow.
# Layer: worker
# Data flow: TEMPORAL_HOST → temporal_worker → workflow/activity execution → unit_levy_ledger / financial_periods (building-scoped).
# Related: backend/workflows/generate_levies_workflow.py (levy generation saga)
#           backend/workflows/reconciliation_close_workflow.py (period close saga)
#           backend/workers/arq_settings.py (ARQ routine jobs — runs alongside)
"""
NOT DEPLOYED (verified live 2026-08-11): `TEMPORAL_HOST` isn't set anywhere in this deployment's
.env, no Temporal server is configured, and no worker process runs. Right-sized architecture
review concluded self-hosting Temporal isn't justified at this app's current scale/criticality (1
real building, non-critical reporting/automation tool) -- see
docs/architecture/financial-postgres-cutover-status-2026-08-11.md for the full reasoning. Left in
place, not deleted, pending a stable release. Reconsider only if a genuine multi-step
approval/durable-timer workflow need arises that the systemd-timer pattern can't cover.

Temporal worker — connects to Temporalite (dev) or Temporal Cloud (prod)
and registers GenerateLeviesWorkflow and ReconciliationCloseWorkflow.

Run:
    cd backend && venv/bin/python3 -m workers.temporal_worker

Environment variables:
    TEMPORAL_HOST        — gRPC target (default: localhost:7233)
    TEMPORAL_NAMESPACE   — Temporal namespace (default: default)
    TEMPORAL_TLS_CERT    — Path to TLS cert (Temporal Cloud only)
    TEMPORAL_TLS_KEY     — Path to TLS key (Temporal Cloud only)
    TEMPORAL_TASK_QUEUE  — Task queue name (default: strata-financial)

Production paths:
    Option A (Temporal Cloud): set TEMPORAL_HOST=<ns>.tmprl.cloud:7233
                                    TEMPORAL_TLS_CERT=<path>
                                    TEMPORAL_TLS_KEY=<path>
    Option B (self-hosted):    set TEMPORAL_HOST=<host>:7233 (no TLS vars)
    Dev (Temporalite):         leave TEMPORAL_HOST unset (localhost:7233)
    See docs/architecture/temporal-production-path.md for full setup guide.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import temporalio.client
import temporalio.worker

from workflows.generate_levies_workflow import (
    GenerateLeviesWorkflow,
    allocate_crns,
    compute_lot_levies,
    create_levy_documents,
    send_levy_notices,
)
from workflows.reconciliation_close_workflow import (
    ReconciliationCloseWorkflow,
    close_period_activity,
    notify_reviewer_activity,
    run_reconciliation_check,
)

logger = logging.getLogger(__name__)

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "strata-financial")
TEMPORAL_TLS_CERT = os.getenv("TEMPORAL_TLS_CERT")
TEMPORAL_TLS_KEY = os.getenv("TEMPORAL_TLS_KEY")


async def _build_client() -> temporalio.client.Client:
    """Build a Temporal client — TLS for Cloud, plaintext for local/self-hosted."""
    tls: bool | temporalio.client.TLSConfig = False
    if TEMPORAL_TLS_CERT and TEMPORAL_TLS_KEY:
        try:
            with open(TEMPORAL_TLS_CERT, "rb") as f:
                cert = f.read()
            with open(TEMPORAL_TLS_KEY, "rb") as f:
                key = f.read()
        except FileNotFoundError as exc:
            logger.error("Temporal TLS file not found: %s", exc)
            raise RuntimeError(f"Temporal TLS certificate file not found: {exc}") from exc
        except PermissionError as exc:
            logger.error("Permission denied reading Temporal TLS file: %s", exc)
            raise RuntimeError(f"Cannot read Temporal TLS certificate file: {exc}") from exc
        except OSError as exc:
            logger.error("Error reading Temporal TLS files: %s", exc)
            raise RuntimeError(f"Failed to read Temporal TLS certificate files: {exc}") from exc
        tls = temporalio.client.TLSConfig(client_cert=cert, client_private_key=key)
        logger.info("Temporal: using mTLS (Temporal Cloud)")
    else:
        logger.info("Temporal: plaintext connection to %s", TEMPORAL_HOST)

    return await temporalio.client.Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        tls=tls,
    )


async def run_worker() -> None:
    """Generated function header.

    Function: run_worker
    Path: backend/workers/temporal_worker.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = await _build_client()
    worker = temporalio.worker.Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[
            GenerateLeviesWorkflow,
            ReconciliationCloseWorkflow,
        ],
        activities=[
            # GenerateLeviesWorkflow activities
            compute_lot_levies,
            allocate_crns,
            create_levy_documents,
            send_levy_notices,
            # ReconciliationCloseWorkflow activities
            run_reconciliation_check,
            notify_reviewer_activity,
            close_period_activity,
        ],
    )
    logger.info(
        "Temporal worker running: host=%s namespace=%s queue=%s",
        TEMPORAL_HOST, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE,
    )
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
