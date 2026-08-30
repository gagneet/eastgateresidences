"""Tests for Tenancy Passport (S5 - A5)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

BUILDING_ID = "13195"
USER_ID = "user-test-passport-001"


@pytest.mark.asyncio
async def test_residency_score_perfect_tenant():
    """Tenant with zero incidents, on-time payments gets score >= 90."""
    mock_db = MagicMock()
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 0})
    mock_db.workflow_requests.count_documents = AsyncMock(return_value=0)
    mock_db.volunteer_events.count_documents = AsyncMock(return_value=2)

    with patch("services.tenancy_passport_service.db", mock_db):
        from services.tenancy_passport_service import compute_residency_score
        result = await compute_residency_score(USER_ID, "TH087", BUILDING_ID)

    assert result["score"] >= 90
    assert result["grade"] == "A"


@pytest.mark.asyncio
async def test_residency_score_noise_complaints_reduce_score():
    """Noise complaints reduce the noise_incidents component."""
    mock_db = MagicMock()
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 0})
    mock_db.workflow_requests.count_documents = AsyncMock(side_effect=[3, 1])  # noise=3, maint=1
    mock_db.volunteer_events.count_documents = AsyncMock(return_value=0)

    with patch("services.tenancy_passport_service.db", mock_db):
        from services.tenancy_passport_service import compute_residency_score
        result = await compute_residency_score(USER_ID, "TH087", BUILDING_ID)

    noise_score = result["components"]["noise_incidents"]
    assert noise_score == 55  # 100 - 3*15 = 55


@pytest.mark.asyncio
async def test_passport_token_roundtrip():
    """Token generated can be verified correctly."""
    from services.tenancy_passport_service import generate_passport_token, verify_passport_token
    token = generate_passport_token(USER_ID, "TH087", 91)
    payload = verify_passport_token(token)
    assert payload is not None
    assert payload["user_id"] == USER_ID
    assert payload["unit_number"] == "TH087"
    assert payload["score"] == 91
    assert payload["type"] == "tenancy_passport"


@pytest.mark.asyncio
async def test_verify_endpoint_returns_partial_name():
    """Verification endpoint only reveals partial name (privacy)."""
    from services.tenancy_passport_service import generate_passport_token
    token = generate_passport_token(USER_ID, "TH087", 88)

    mock_db = MagicMock()
    mock_db._db.users.find_one = AsyncMock(return_value={"full_name": "Avneet Singh"})

    with patch("routers.residency.db", mock_db):
        from routers.residency import verify_passport
        result = await verify_passport(token)

    assert result["valid"] is True
    assert result["resident_name_partial"] == "A. Singh"
    assert "Avneet" not in result["resident_name_partial"]
    assert "pitch" in result


@pytest.mark.asyncio
async def test_verify_invalid_token_raises_404():
    """Invalid token raises HTTP 404."""
    with patch("routers.residency.db", MagicMock()):
        from routers.residency import verify_passport
        with pytest.raises(HTTPException) as exc:
            await verify_passport("invalid.token.here")
    assert exc.value.status_code == 404


def test_pdf_generation_returns_bytes():
    """PDF generation returns non-empty bytes."""
    from services.tenancy_passport_service import generate_passport_pdf
    pdf = generate_passport_pdf(
        resident_name="Test Resident",
        unit_number="TH087",
        building_name="East Gate Residences",
        lease_start="2024-01-01",
        lease_end="2026-01-01",
        score=88,
        score_components={"payment_history": 100, "noise_incidents": 100,
                          "maintenance_requests": 80, "community_engagement": 50},
        noise_incidents=0,
        maintenance_count=2,
        user_id=USER_ID,
    )
    assert isinstance(pdf, bytes)
    assert len(pdf) > 0
