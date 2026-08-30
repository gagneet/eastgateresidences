import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
from bson import ObjectId

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), "backend"))

# Mock database.db BEFORE importing matching
mock_db = MagicMock()
with patch("database.db", mock_db):
    import routers.matching as matching


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


async def test_run_auto_match_robust():
    print("Running test_run_auto_match_robust (Volume/Lock/Transaction)...")
    mock_user = {"_id": ObjectId("507f1f77bcf86cd799439011"), "id": "u1", "role": "super_admin"}

    # Setup data
    invoice = _make_invoice("1", 100.0, "2026-03-10T10:00:00")
    tx = _make_tx("a", 100.0, "2026-03-11T12:00:00")

    db = MagicMock()
    # Volume Validation Mocks
    db.invoices.count_documents = AsyncMock(return_value=1)
    db.bank_transactions.count_documents = AsyncMock(return_value=1)

    # Lock Mocks
    db.locks.insert_one = AsyncMock()
    db.locks.delete_one = AsyncMock()

    # Data Fetch Mocks
    db.invoices.find.return_value.to_list = AsyncMock(return_value=[invoice])
    db.bank_transactions.find.return_value.to_list = AsyncMock(return_value=[tx])

    # Bulk write mocks
    db.invoices.bulk_write = AsyncMock()
    db.bank_transactions.bulk_write = AsyncMock()
    db.matching_results.bulk_write = AsyncMock()

    # Session mock for transaction
    mock_session = AsyncMock()
    db._db.client.start_session = AsyncMock(return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session
    mock_session.start_transaction = MagicMock()

    with patch("routers.matching._get_db", return_value=db), \
            patch("routers.matching._require_finance", return_value=mock_user), \
            patch("routers.matching.create_audit_log", AsyncMock()):
        result = await matching.run_auto_match(
            building_id="b1",
            tolerance_aud=0.01,
            date_window_days=7,
            current_user=mock_user
        )

    assert result["matches_found"] == 1
    assert db.locks.insert_one.called
    assert db.locks.delete_one.called
    assert db._db.client.start_session.called
    print("SUCCESS: test_run_auto_match_robust passed")


async def test_volume_exception():
    print("Running test_volume_exception...")
    mock_user = {"_id": ObjectId("507f1f77bcf86cd799439011"), "id": "u1", "role": "super_admin"}
    db = MagicMock()
    db.invoices.count_documents = AsyncMock(return_value=2000)  # Exceeds MAX_INVOICES
    db.bank_transactions.count_documents = AsyncMock(return_value=1)

    from fastapi import HTTPException
    with patch("routers.matching._get_db", return_value=db), \
            patch("routers.matching._require_finance", return_value=mock_user):

        try:
            await matching.run_auto_match(building_id="b1", current_user=mock_user)
            assert False, "Should have raised 400"
        except HTTPException as e:
            assert e.status_code == 400
            assert "too large" in e.detail
    print("SUCCESS: test_volume_exception passed")


async def main():
    try:
        await test_run_auto_match_robust()
        await test_volume_exception()
        print("\nAll robust matching tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
