"""
Regression test: get_sinking_fund_forecast_pg() must return a `contributions`
field on every projection row.

ReserveRunwayChart plots a "Contributions" area series alongside "Balance", reading
row.contributions ?? row.contribution ?? 0. The Postgres forecast path computed the
per-year levy contribution to advance running_balance but never included it in the
returned row, so the Contributions area was always flat/zero for any building on the
Postgres cutover path, despite the function's own docstring claiming shape parity
with the MongoDB forecast (services/forecast_service.py).
"""
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_projection_rows_include_contributions():
    from services.analytics_pg_service import get_sinking_fund_forecast_pg

    scheme = {
        "scheme_id": uuid.UUID("33333333-0000-0000-0000-000000000000"),
        "tenant_id": uuid.UUID("44444444-0000-0000-0000-000000000000"),
    }

    balance_row = MagicMock()
    balance_row.__getitem__ = MagicMock(side_effect=lambda i: [500000][i])  # $5,000.00 net
    balance_result = MagicMock()
    balance_result.fetchone = MagicMock(return_value=balance_row)

    current_year = __import__("datetime").datetime.now().year

    def _year_row(year, cents):
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda i: [year, cents][i])
        return r

    year_rows = [
        _year_row(current_year + 1, 1000000),  # $10,000.00 contribution
        _year_row(current_year + 2, 1500000),  # $15,000.00 contribution
    ]
    year_result = MagicMock()
    year_result.fetchall = MagicMock(return_value=year_rows)

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=[balance_result, year_result])
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.analytics_pg_service._resolve_scheme", AsyncMock(return_value=scheme)), \
         patch("db_postgres.session.async_session_context", return_value=mock_session), \
         patch("db_postgres.session.set_tenant", AsyncMock()):
        result = await get_sinking_fund_forecast_pg("13195", years=3)

    assert result["current_balance"] == 5000.0
    projection = {row["year"]: row for row in result["projection"]}

    assert projection[current_year + 1]["contributions"] == 10000.0
    assert projection[current_year + 1]["balance"] == 15000.0  # 5000 + 10000
    assert projection[current_year + 1]["is_actual"] is True

    assert projection[current_year + 2]["contributions"] == 15000.0
    assert projection[current_year + 2]["balance"] == 30000.0  # 15000 + 15000

    # A year with no matching levy_runs row still gets an explicit 0, not a missing key.
    assert projection[current_year + 3]["contributions"] == 0.0
