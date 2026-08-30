"""Tests for routers/arrears_risk.py — GAP-FIN-006."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

BUILDING_A = "16244"
BUILDING_B = "13195"

_MANAGER = {"id": "mgr-1", "role": "strata_manager", "full_name": "Test Manager"}
_CHAIRMAN = {"id": "chr-1", "role": "ec_member", "full_name": "Chairman"}
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}

_UNIT_5_RISK = {
    "unit_number": "5",
    "outstanding_aud": 1530.30,
    "score": 72,
    "band": "HIGH",
    "signals": ["overdue_90d", "repeat_offender"],
    "hardship_flag": False,
    "form1_eligible": True,
}
_UNIT_7_RISK = {
    "unit_number": "7",
    "outstanding_aud": 250.00,
    "score": 25,
    "band": "LOW",
    "signals": [],
    "hardship_flag": False,
    "form1_eligible": False,
}
_UNIT_12_CRITICAL = {
    "unit_number": "12",
    "outstanding_aud": 6120.60,
    "score": 95,
    "band": "CRITICAL",
    "signals": ["overdue_180d", "repeat_offender", "legal_action_risk"],
    "hardship_flag": True,
    "form1_eligible": True,
}


@pytest.mark.asyncio
async def test_list_arrears_risk_returns_all_overdue_units():
    from routers.arrears_risk import list_arrears_risk
    units = [_UNIT_5_RISK, _UNIT_7_RISK, _UNIT_12_CRITICAL]
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("routers.arrears_risk._require_finance", return_value=_MANAGER), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=units) as mock_compute:
        result = await list_arrears_risk(building_id=BUILDING_A, current_user=_MANAGER)
    assert result["total"] == 3
    assert len(result["data"]) == 3
    mock_compute.assert_called_once()
    _, kwargs = mock_compute.call_args
    # building_id must be passed through
    assert BUILDING_A in mock_compute.call_args[0] or kwargs.get("building_id") == BUILDING_A


@pytest.mark.asyncio
async def test_unit_arrears_risk_returns_matching_unit():
    from routers.arrears_risk import unit_arrears_risk
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("routers.arrears_risk._require_finance", return_value=_MANAGER), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=[_UNIT_5_RISK, _UNIT_12_CRITICAL]):
        result = await unit_arrears_risk(unit_number="5", building_id=BUILDING_A, current_user=_MANAGER)
    assert result["unit_number"] == "5"
    assert result["band"] == "HIGH"
    assert result["form1_eligible"] is True


@pytest.mark.asyncio
async def test_unit_arrears_risk_returns_zero_risk_when_no_arrears():
    from routers.arrears_risk import unit_arrears_risk
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("routers.arrears_risk._require_finance", return_value=_MANAGER), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=[]):
        result = await unit_arrears_risk(unit_number="99", building_id=BUILDING_A, current_user=_MANAGER)
    assert result["unit_number"] == "99"
    assert result["band"] == "LOW"
    assert result["score"] == 0
    assert result["outstanding_aud"] == 0.0


@pytest.mark.asyncio
async def test_arrears_summary_band_distribution():
    from routers.arrears_risk import arrears_risk_summary
    units = [_UNIT_5_RISK, _UNIT_7_RISK, _UNIT_12_CRITICAL]
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("routers.arrears_risk._require_finance", return_value=_MANAGER), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=units):
        result = await arrears_risk_summary(building_id=BUILDING_A, current_user=_MANAGER)
    assert result["building_id"] == BUILDING_A
    assert result["units_with_arrears"] == 3
    assert result["band_distribution"]["HIGH"] == 1
    assert result["band_distribution"]["LOW"] == 1
    assert result["band_distribution"]["CRITICAL"] == 1
    assert result["hardship_flagged"] == 1
    assert result["form1_eligible"] == 2
    assert abs(result["total_outstanding_aud"] - 7900.90) < 0.01


@pytest.mark.asyncio
async def test_owner_role_denied():
    from routers.arrears_risk import list_arrears_risk
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await list_arrears_risk(building_id=BUILDING_A, current_user=_OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_chairman_role_allowed():
    from routers.arrears_risk import list_arrears_risk
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=[]):
        result = await list_arrears_risk(building_id=BUILDING_A, current_user=_CHAIRMAN)
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_cross_building_isolation():
    """compute_arrears_risk must receive the correct building_id, not bleed across tenants."""
    from routers.arrears_risk import list_arrears_risk
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("routers.arrears_risk._require_finance", return_value=_MANAGER), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=[]) as mock_compute:
        await list_arrears_risk(building_id=BUILDING_B, current_user=_MANAGER)
    # The call must have passed BUILDING_B, not BUILDING_A
    args = mock_compute.call_args[0]
    assert BUILDING_B in args
    assert BUILDING_A not in args


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner():
    """effective_role='ec_member' on a raw-owner user must grant arrears risk access."""
    from routers.arrears_risk import list_arrears_risk
    elevated_owner = {"id": "eo-1", "role": "owner", "effective_role": "ec_member"}
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=[]):
        result = await list_arrears_risk(building_id=BUILDING_A, current_user=elevated_owner)
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_effective_role_blocks_downgraded_manager():
    """effective_role='guest' on a raw-manager user must be blocked."""
    from routers.arrears_risk import list_arrears_risk
    from fastapi import HTTPException
    downgraded = {"id": "dm-1", "role": "strata_manager", "effective_role": "guest"}
    db = MagicMock()
    with patch("routers.arrears_risk._get_db", return_value=db), \
            patch("services.arrears_analytics.compute_arrears_risk",
                  new_callable=AsyncMock, return_value=[]):
        with pytest.raises(HTTPException) as exc:
            await list_arrears_risk(building_id=BUILDING_A, current_user=downgraded)
    assert exc.value.status_code == 403
