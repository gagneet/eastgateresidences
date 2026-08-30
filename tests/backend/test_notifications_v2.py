import uuid

import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def test_notification_endpoints():
    # Check if routes exist (should return 401 Unauthorized since we're not logged in)
    get_endpoints = [
        "/api/notifications/unread-count",
        "/api/notifications/unread",
        "/api/notifications/history",
        "/api/notifications/admin/audit-logs"
    ]

    for endpoint in get_endpoints:
        response = client.get(endpoint)
        assert response.status_code == 401, f"Endpoint {endpoint} failed with {response.status_code}"

    # Test PUT endpoint separately
    response = client.put("/api/notifications/read-all")
    assert response.status_code == 401, f"Endpoint /api/notifications/read-all failed with {response.status_code}"


def test_mark_as_read_fails_without_auth():
    notif_id = str(uuid.uuid4())
    response = client.put(f"/api/notifications/{notif_id}/read")
    assert response.status_code == 401


def test_config_sender_email():
    from config import SENDER_EMAIL
    assert SENDER_EMAIL == "noreply@eastgateresidences.com.au"
