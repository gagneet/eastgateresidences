"""Tests for Postgres users read-path response normalisation."""

from services.users_pg_service import _pg_row_to_user_dict


def test_pg_row_to_user_dict_preserves_ec_position():
    row = {
        "id": "user-1",
        "email": "chair@example.com",
        "full_name": "Committee Chair",
        "role": "ec_member",
        "effective_role": "ec_member",
        "ec_position": "CHAIRMAN",
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "lot_numbers": [],
    }

    result = _pg_row_to_user_dict(row, "13195")

    assert result["role"] == "ec_member"
    assert result["ec_position"] == "CHAIRMAN"
    assert result["building_id"] == "13195"
