"""
Unit tests for routers/utilities.py — DB-driven path (audit Step 4b).

After migration 018, East Gate meter data lives in the `unit_utilities` collection
rather than a hardcoded `if building_id == "13195":` branch.  These tests verify:

  - East Gate units are served from unit_utilities (not the old static-map branch)
  - Other buildings with utility data in unit_utilities are served correctly
  - Buildings with no unit_utilities data receive the generic fallback
  - Cross-building isolation: building A cannot read building B's utility docs
  - The `building_id == "13195"` literal no longer appears in the router source

All tests mock the DB directly — no running backend required.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_unit_doc(unit_number="UA001", lot_number=1, building_id="13195"):
    return {"building_id": building_id, "unit_number": unit_number, "lot_number": lot_number}


def _mock_util_doc(unit_number, lot_number, loc_id, nmi, has_gas, building_id="13195"):
    """Minimal unit_utilities document mirroring the structure stored by migration 018."""
    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "lot_number": lot_number,
        "electricity": {
            "unit_number": unit_number, "lot_number": lot_number,
            "loc_id": loc_id, "supplier": "Origin Energy",
            "distributor": "Evoenergy (ActewAGL)",
            "supply_charge_per_day": 1.46784, "usage_charge_per_unit": 0.25674,
            "faults_emergency": "1800 002 438", "assistance": "1300 137 427",
            "my_account_url": "https://www.originenergy.com.au/myaccount",
        },
        "gas": {
            "unit_number": unit_number, "lot_number": lot_number,
            "has_gas": has_gas,
            "cooktop_type": "Gas" if has_gas else "Induction",
            "meter_number": "EC 930 999" if has_gas else None,
            "supplier": "Origin Gas" if has_gas else None,
            "distributor": "Evoenergy",
            "faults_evoenergy": "1300 137 078", "emergencies_evoenergy": "13 19 09",
        },
        "nbn": {
            "unit_number": unit_number, "lot_number": lot_number,
            "nmi": nmi, "property_number": "ED099999999",
            "supplier": "National Broadband Network Pty Ltd",
            "note": "Contact your chosen retail service provider (RSP) to connect.",
        },
    }


def _make_db(unit_doc=None, util_doc=None):
    """Build a mock db where find_one returns the given docs for each collection."""
    mock_db = MagicMock()

    async def find_one_units(query, *args, **kwargs):
        if unit_doc and query.get("building_id") == unit_doc.get("building_id"):
            return unit_doc
        return None

    async def find_one_utilities(query, *args, **kwargs):
        if util_doc and query.get("building_id") == util_doc.get("building_id"):
            return util_doc
        return None

    mock_db.units.find_one = find_one_units
    mock_db.unit_utilities.find_one = find_one_utilities
    return mock_db


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestUtilitiesDbPath:
    """Functional tests for the DB-backed utilities endpoint."""

    @pytest.mark.asyncio
    async def test_east_gate_unit_served_from_unit_utilities(self):
        """UA001 returns East Gate's real LOC ID from unit_utilities, not the static map."""
        unit_doc = _mock_unit_doc("UA001", lot_number=1, building_id="13195")
        util_doc = _mock_util_doc("UA001", 1, "LOC000186230612", "ENNE019259",
                                  has_gas=False, building_id="13195")
        mock_db = _make_db(unit_doc=unit_doc, util_doc=util_doc)

        privileged_user = {"role": "super_admin", "unit_number": None}

        with patch("routers.utilities.db", mock_db), \
                patch("routers.utilities.get_current_user", return_value=privileged_user), \
                patch("routers.utilities.get_current_building", return_value="13195"), \
                patch("routers.utilities.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)

            from routers.utilities import get_utilities
            result = await get_utilities(
                unit_number="UA001",
                current_user=privileged_user,
                building_id="13195",
            )

        assert result.electricity.loc_id == "LOC000186230612"
        assert result.nbn.nmi == "ENNE019259"
        assert result.gas.has_gas is False
        assert result.lot_number == 1

    @pytest.mark.asyncio
    async def test_townhouse_with_gas_served_correctly(self):
        """TH087 (lot 87, has gas) returns gas data from unit_utilities."""
        unit_doc = _mock_unit_doc("TH087", lot_number=87, building_id="13195")
        util_doc = _mock_util_doc("TH087", 87, "LOC000186231477", "ENNE019345",
                                  has_gas=True, building_id="13195")
        mock_db = _make_db(unit_doc=unit_doc, util_doc=util_doc)

        privileged_user = {"role": "super_admin", "unit_number": None}

        with patch("routers.utilities.db", mock_db), \
                patch("routers.utilities.get_current_user", return_value=privileged_user), \
                patch("routers.utilities.get_current_building", return_value="13195"), \
                patch("routers.utilities.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)

            from routers.utilities import get_utilities
            result = await get_utilities(
                unit_number="TH087",
                current_user=privileged_user,
                building_id="13195",
            )

        assert result.gas.has_gas is True
        assert result.gas.cooktop_type == "Gas"
        assert result.electricity.loc_id == "LOC000186231477"

    @pytest.mark.asyncio
    async def test_other_building_with_utility_data_served_correctly(self):
        """A non-East-Gate building with a unit_utilities record is served correctly."""
        unit_doc = _mock_unit_doc("UA101", lot_number=101, building_id="16244")
        util_doc = _mock_util_doc("UA101", 101, "LOC_SIERRA_101", "NMI_SIERRA_101",
                                  has_gas=False, building_id="16244")
        mock_db = _make_db(unit_doc=unit_doc, util_doc=util_doc)

        privileged_user = {"role": "super_admin", "unit_number": None}

        with patch("routers.utilities.db", mock_db), \
                patch("routers.utilities.get_current_user", return_value=privileged_user), \
                patch("routers.utilities.get_current_building", return_value="16244"), \
                patch("routers.utilities.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)

            from routers.utilities import get_utilities
            result = await get_utilities(
                unit_number="UA101",
                current_user=privileged_user,
                building_id="16244",
            )

        assert result.electricity.loc_id == "LOC_SIERRA_101"
        assert result.nbn.nmi == "NMI_SIERRA_101"

    @pytest.mark.asyncio
    async def test_building_with_no_utility_data_returns_generic_fallback(self):
        """A building with a unit but no unit_utilities entry returns generic fallback."""
        unit_doc = _mock_unit_doc("UA001", lot_number=5, building_id="16244")
        # No util_doc — unit_utilities returns None
        mock_db = _make_db(unit_doc=unit_doc, util_doc=None)

        privileged_user = {"role": "super_admin", "unit_number": None}

        with patch("routers.utilities.db", mock_db), \
                patch("routers.utilities.get_current_user", return_value=privileged_user), \
                patch("routers.utilities.get_current_building", return_value="16244"), \
                patch("routers.utilities.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)

            from routers.utilities import get_utilities
            result = await get_utilities(
                unit_number="UA001",
                current_user=privileged_user,
                building_id="16244",
            )

        # Generic fallback: no LOC ID, no NMI, no gas
        assert result.electricity.loc_id is None
        assert result.nbn.nmi is None
        assert result.gas.has_gas is False
        assert result.lot_number == 5

    @pytest.mark.asyncio
    async def test_cross_building_isolation(self):
        """Sierra (16244) cannot read East Gate (13195) utility documents."""
        # East Gate has util_doc stored; Sierra has a unit but no utility data
        east_gate_util = _mock_util_doc("UA001", 1, "LOC000186230612", "ENNE019259",
                                        has_gas=False, building_id="13195")
        sierra_unit_doc = _mock_unit_doc("UA001", lot_number=5, building_id="16244")

        # DB returns East Gate util doc only when queried with building_id="13195"
        mock_db = _make_db(unit_doc=sierra_unit_doc, util_doc=east_gate_util)

        privileged_user = {"role": "super_admin", "unit_number": None}

        with patch("routers.utilities.db", mock_db), \
                patch("routers.utilities.get_current_user", return_value=privileged_user), \
                patch("routers.utilities.get_current_building", return_value="16244"), \
                patch("routers.utilities.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_view_finances=True)

            from routers.utilities import get_utilities
            result = await get_utilities(
                unit_number="UA001",
                current_user=privileged_user,
                building_id="16244",
            )

        # Sierra UA001 must NOT receive East Gate's LOC ID
        assert result.electricity.loc_id is None, (
            f"Cross-building leak: Sierra got East Gate LOC ID {result.electricity.loc_id!r}"
        )


class TestNoHardcodedBuildingId:
    """Static source check — building_id == '13195' must not appear in the router."""

    def test_hardcoded_13195_branch_removed_from_router(self):
        """The `if building_id == '13195':` branch must not exist in utilities.py.
        This is a regression guard for audit finding R1."""
        import routers.utilities as utilities_module
        source = inspect.getsource(utilities_module)
        # Accept plan_number constant at module top but reject runtime branch
        branch_pattern = 'if building_id == "13195"'
        assert branch_pattern not in source, (
            f"Hardcoded tenant branch found in utilities.py: {branch_pattern!r}\n"
            "This was removed by audit Step 4b (migration 018). "
            "Do not re-add this check — use the unit_utilities collection instead."
        )
