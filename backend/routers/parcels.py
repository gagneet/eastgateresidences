# @featuretrace:bookings — Parcel arrival notification hook (event-driven, not a router).
# Layer: worker
# Data flow: parcels.insert event → notify_parcel_arrival() → no-op (real notifications fire via change-stream worker watching db.parcels, building-scoped)
# Related: backend/server.py (POST/GET/PUT /parcels endpoints, lines ~12081-12215)
#           backend/workers/notification_worker.py
#           backend/workers/change_stream_worker.py
# Collection: parcels
# Scope: (building-scoped)
"""
Parcel arrival notifications — event-triggered when a parcel is logged at reception.
The notify_parcel_arrival entrypoint can also be called by the workflow runner for
health-check / manual dispatch purposes.

NOTE: This file is NOT a FastAPI router (no APIRouter, no HTTP endpoints).
The parcel CRUD endpoints live in server.py (~line 12081). This module is a
workflow-catalogue entrypoint only — manual dispatch is intentionally a no-op.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def notify_parcel_arrival(building_id: str | None = None) -> None:
    """
    Placeholder entrypoint for the workflow catalogue.
    Parcel notifications are event-triggered (fired on parcels.insert),
    not scheduled — manual dispatch is a no-op by design.
    """
    logger.info(
        "notify_parcel_arrival called for building %s — event-driven, no batch action.",
        building_id,
    )
