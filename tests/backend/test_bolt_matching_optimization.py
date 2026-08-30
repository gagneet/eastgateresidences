from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId


# Mock the database and auth
@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_user():
    return {
        "_id": ObjectId("507f1f77bcf86cd799439011"),
        "id": "user-123",
        "role": "super_admin",
        "full_name": "Test Admin"
    }


def _make_invoice(id_str, amount, date_str, po_id=None):
    return {
        "_id": ObjectId(id_str.ljust(24, '0')),
        "id": id_str,
        "amount": amount,
        "invoice_date": date_str,
        "status": "pending",
        "matched_tx_id": None,
        "po_id": po_id,
        "building_id": "b1"
    }


def _make_tx(id_str, amount, date_str):
    return {
        "_id": ObjectId(id_str.ljust(24, '0')),
        "id": id_str,
        "amount": amount,
        "date": date_str,
        "matched": False,
        "type": "debit",
        "building_id": "b1"
    }


@pytest.mark.asyncio
async def test_run_auto_match_success(mock_user):
    """Test successful auto-match with exact amount and date within window."""
    import routers.matching as matching

    mock_db = MagicMock()
    # Robustness Mocks
    mock_db.invoices.count_documents = AsyncMock(return_value=1)
    mock_db.bank_transactions.count_documents = AsyncMock(return_value=1)
    mock_db.locks.insert_one = AsyncMock()
    mock_db.locks.delete_one = AsyncMock()

    # Session mock for transaction
    mock_session = AsyncMock()
    mock_db._db.client.start_session = AsyncMock(return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session
    mock_session.start_transaction = MagicMock()

    # Setup data: 1 invoice, 1 matching transaction
    inv_date = "2026-03-10T10:00:00"
    tx_date = "2026-03-11T12:00:00"  # Within 7 days
    invoice = _make_invoice("1", 100.0, inv_date)
    tx = _make_tx("a", 100.0, tx_date)

    # Mock find().to_list() pattern
    mock_db.invoices.find.return_value.to_list = AsyncMock(return_value=[invoice])
    mock_db.bank_transactions.find.return_value.to_list = AsyncMock(return_value=[tx])

    # Mock bulk writes
    mock_db.invoices.bulk_write = AsyncMock()
    mock_db.bank_transactions.bulk_write = AsyncMock()
    mock_db.matching_results.bulk_write = AsyncMock()

    with patch("routers.matching._get_db", return_value=mock_db), \
            patch("routers.matching._require_finance", return_value=mock_user), \
            patch("routers.matching.create_audit_log", AsyncMock()):
        result = await matching.run_auto_match(
            building_id="b1",
            tolerance_aud=0.01,
            date_window_days=7,
            current_user=mock_user
        )

    assert result["matches_found"] == 1
    assert result["invoices_processed"] == 1

    # Verify robust patterns
    assert mock_db.locks.insert_one.called
    assert mock_db.invoices.bulk_write.called
    assert mock_db.bank_transactions.bulk_write.called
    assert mock_db.matching_results.bulk_write.called


@pytest.mark.asyncio
async def test_run_auto_match_outside_tolerance(mock_user):
    """Test no match when amount is outside tolerance."""
    import routers.matching as matching

    mock_db = MagicMock()
    mock_db.invoices.count_documents = AsyncMock(return_value=1)
    mock_db.bank_transactions.count_documents = AsyncMock(return_value=1)
    mock_db.locks.insert_one = AsyncMock()
    mock_db.locks.delete_one = AsyncMock()

    inv_date = "2026-03-10T10:00:00"
    invoice = _make_invoice("1", 100.0, inv_date)
    tx = _make_tx("a", 200.0, "2026-03-11T12:00:00")

    mock_db.invoices.find.return_value.to_list = AsyncMock(return_value=[invoice])
    mock_db.bank_transactions.find.return_value.to_list = AsyncMock(return_value=[tx])
    mock_db.invoices.bulk_write = AsyncMock()

    with patch("routers.matching._get_db", return_value=mock_db), \
            patch("routers.matching._require_finance", return_value=mock_user):
        result = await matching.run_auto_match(
            building_id="b1",
            tolerance_aud=0.01,
            date_window_days=7,
            current_user=mock_user
        )

    assert result["matches_found"] == 0
    assert result["invoices_processed"] == 1
    assert not mock_db.invoices.bulk_write.called


@pytest.mark.asyncio
async def test_run_auto_match_outside_date_window(mock_user):
    """Test no match when date is outside window."""
    import routers.matching as matching

    mock_db = MagicMock()
    mock_db.invoices.count_documents = AsyncMock(return_value=1)
    mock_db.bank_transactions.count_documents = AsyncMock(return_value=1)
    mock_db.locks.insert_one = AsyncMock()
    mock_db.locks.delete_one = AsyncMock()

    inv_date = "2026-03-10T10:00:00"
    invoice = _make_invoice("1", 100.0, inv_date)
    tx = _make_tx("a", 100.0, "2026-03-20T12:00:00")  # 10 days later

    mock_db.invoices.find.return_value.to_list = AsyncMock(return_value=[invoice])
    mock_db.bank_transactions.find.return_value.to_list = AsyncMock(return_value=[tx])

    with patch("routers.matching._get_db", return_value=mock_db), \
            patch("routers.matching._require_finance", return_value=mock_user):
        result = await matching.run_auto_match(
            building_id="b1",
            tolerance_aud=0.01,
            date_window_days=7,
            current_user=mock_user
        )

    assert result["matches_found"] == 0
