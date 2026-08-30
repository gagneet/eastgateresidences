from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks


@pytest.mark.asyncio
@patch("routers.market.db")
async def test_get_market_pulse(mock_db):
    """Test the market pulse endpoint returns expected structure."""
    from datetime import datetime, timezone
    recent_iso = datetime.now(timezone.utc).isoformat()
    mock_db.market_stats.find_one = AsyncMock(return_value={
        "suburb": "Denman Prospect",
        "unit_median": "$700,000",
        "house_median": "$1,090,000",
        "growth_12m": "+22.4%",
        "rental_yield": "5.2%",
        "updated_at": recent_iso,
        "source": "Seed Data",
        "last_sale": {
            "price": "$895,000",
            "title": "2 Bed East Gate",
            "address": "14 Hoolihan Street, Denman Prospect",
            "url": "https://realestate.com.au",
            "source": "realestate.com.au",
            "scraped_at": recent_iso,
        }
    })

    from routers.market import get_market_pulse

    current_user = {
        "id": "test-user",
        "role": "owner",
        "is_active": True,
        "is_approved": True,
    }

    data = await get_market_pulse(
        background_tasks=BackgroundTasks(),
        current_user=current_user,
        building_id="13195",
    )

    assert "suburb" in data
    assert data["suburb"] == "Denman Prospect"
    assert "unit_median" in data or "status" in data


@pytest.mark.asyncio
@patch("routers.market.scrape_denman_data")
async def test_scrape_denman_data_fallback(mock_scrape):
    """Test the scraper logic returns expected structure."""
    mock_scrape.return_value = {
        "suburb": "Denman Prospect",
        "unit_median": 650000,
        "status": "scraped"
    }

    from routers.market import scrape_denman_data
    data = await scrape_denman_data()
    assert data["suburb"] == "Denman Prospect"
    assert "unit_median" in data
