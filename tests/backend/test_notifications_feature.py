import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def test_notification_endpoints_exist():
    # Check if routes exist (should return 401 Unauthorized since we're not logged in)
    endpoints = [
        "/api/notifications/unread-count",
        "/api/notifications/unread",
        "/api/notifications/history",
        "/api/notifications/read-all",
        "/api/notifications/admin/audit-logs"
    ]

    for endpoint in endpoints:
        if "read-all" in endpoint:
            response = client.put(endpoint)
        else:
            response = client.get(endpoint)
        assert response.status_code == 401, f"Endpoint {endpoint} failed with {response.status_code}"


def test_config_sender_email():
    from config import SENDER_EMAIL
    assert SENDER_EMAIL == "noreply@eastgateresidences.com.au"
