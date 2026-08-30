#!/usr/bin/env python3
"""Remove known synthetic test records that leaked into East Gate UI surfaces.

Dry-run by default. Use --apply to delete. The filters are intentionally narrow:
East Gate records flagged as test data, the performance-test title/description,
and the explicit Test Registrant notification/activity strings. Email logs do not
always carry building_id, so that cleanup is limited to the same synthetic subject
patterns instead of a broad building filter.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from pymongo import MongoClient


BUILDING_ID = "13195"
PERF_DESCRIPTION = "Automated performance test \u2014 safe to delete"


def _load_backend_env() -> None:
    """Generated function header.

    Function: _load_backend_env
    Path: backend/scripts/cleanup_eastgate_test_data.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _delete_or_count(collection: Any, query: dict[str, Any], apply: bool) -> int:
    """Generated function header.

    Function: _delete_or_count
    Path: backend/scripts/cleanup_eastgate_test_data.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if apply:
        return int(collection.delete_many(query).deleted_count)
    return int(collection.count_documents(query))


def _matching_ids(collection: Any, query: dict[str, Any]) -> list[str]:
    """Generated function header.

    Function: _matching_ids
    Path: backend/scripts/cleanup_eastgate_test_data.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return [doc["id"] for doc in collection.find(query, {"_id": 0, "id": 1}) if doc.get("id")]


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/cleanup_eastgate_test_data.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete matching records instead of dry-running")
    parser.add_argument("--building-id", default=BUILDING_ID, help="Building id to clean, default: 13195")
    args = parser.parse_args()

    _load_backend_env()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME are required in environment or backend/.env")

    client = MongoClient(mongo_url)
    db = client[db_name]

    workflow_query = {
        "building_id": args.building_id,
        "$or": [
            {"is_test_data": True},
            {"title": {"$regex": r"^Perf test request perf"}},
            {"subject": {"$regex": r"^Perf test request perf"}},
            {"description": PERF_DESCRIPTION},
            {"body": PERF_DESCRIPTION},
        ],
    }
    maintenance_query = {
        "building_id": args.building_id,
        "$or": [
            {"is_test_data": True},
            {"title": {"$regex": r"^Perf test request perf"}},
            {"description": PERF_DESCRIPTION},
        ],
    }
    activity_query = {
        "building_id": args.building_id,
        "$or": [
            {"title": "New Resident Joined: Test Registrant"},
            {"title": {"$regex": r"^New Maintenance Request: Perf test request perf"}},
        ],
    }
    email_log_query = {
        "$or": [
            {"subject": {"$regex": r"Perf test request perf"}},
            {"subject": {"$regex": r"Test Registrant"}},
        ],
    }
    maintenance_ids = _matching_ids(db.maintenance_requests, maintenance_query)
    audit_log_query = {
        "resource_type": "maintenance_request",
        "resource_id": {"$in": maintenance_ids},
    }
    notification_query = {
        "building_id": args.building_id,
        "$or": [
            {"title": "New Resident Joined: Test Registrant"},
            {"message": {"$regex": "Test Registrant"}},
            {"title": {"$regex": r"Perf test request perf"}},
            {"message": {"$regex": r"Perf test request perf"}},
        ],
    }

    mode = "deleted" if args.apply else "matched"
    workflow_count = _delete_or_count(db.workflow_requests, workflow_query, args.apply)
    audit_log_count = _delete_or_count(db.audit_logs, audit_log_query, args.apply) if maintenance_ids else 0
    maintenance_count = _delete_or_count(db.maintenance_requests, maintenance_query, args.apply)
    activity_count = _delete_or_count(db.activities, activity_query, args.apply)
    email_log_count = _delete_or_count(db.email_sent_log, email_log_query, args.apply)
    notification_count = _delete_or_count(db.user_notifications, notification_query, args.apply)
    print(f"workflow_requests {mode}: {workflow_count}")
    print(f"maintenance_requests {mode}: {maintenance_count}")
    print(f"activities {mode}: {activity_count}")
    print(f"email_sent_log {mode}: {email_log_count}")
    print(f"audit_logs {mode}: {audit_log_count}")
    print(f"user_notifications {mode}: {notification_count}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
