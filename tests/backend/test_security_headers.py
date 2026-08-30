from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# server is already imported by earlier test files; just import app from cache.
from server import app

client = TestClient(app)


def test_security_headers():
    """
    Test that the security headers are correctly added to the response.
    """
    response = client.get("/api")

    # Verify the headers are present and have the correct values
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"


@patch('os.getenv')
def test_hsts_header_production(mock_getenv):
    """
    Test that HSTS header is added only in production.
    """
    mock_getenv.return_value = "production"
    response = client.get("/api")
    assert "Strict-Transport-Security" in response.headers
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]


@patch('os.getenv')
def test_hsts_header_development(mock_getenv):
    """
    Test that HSTS header is NOT added in development.
    """
    mock_getenv.return_value = "development"
    response = client.get("/api")
    assert "Strict-Transport-Security" not in response.headers


if __name__ == "__main__":
    # If run directly, just test once
    try:
        test_security_headers()
        print("✅ Security headers test passed!")
    except Exception as e:
        print(f"❌ Security headers test failed: {e}")
