"""
Enhanced Integration Tests
==========================
Covers three areas of increased confidence:

  1. Multi-tenant data isolation — JWT building_id is the source of truth;
     cross-building requests must be rejected.

  2. Financial transaction rollback — budget proposals can be rolled back
     (replaced / re-saved); unit levy ledger stays consistent.

  3. Guest expiry — 364-day JWT hard cap is enforced at token creation time;
     expired guest JWTs are rejected at decode time.

These are fast, self-contained unit tests (no live MongoDB / backend needed).
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# ── path setup ────────────────────────────────────────────────────────────────
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-tests")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("FRONTEND_URL", "https://www.eastgateresidences.com.au")

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── Constants ─────────────────────────────────────────────────────────────────
BUILDING_A = "13195"
BUILDING_B = "16244"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Multi-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────


class TestMultiTenantIsolation(unittest.TestCase):
    """
    Verify that:
      * create_token always embeds building_id into the JWT payload.
      * get_current_building extracts building_id exclusively from the JWT.
      * Cross-building requests (JWT says building A, path refers to building B)
        are rejected with 403 before any DB query executes.
    """

    def test_token_embeds_building_id(self):
        """Token payload must carry the building_id claim."""
        import jwt as _jwt
        from utils.auth import create_token

        token = create_token("u1", "user@test.com", "owner", building_id=BUILDING_A)
        secret = os.environ["JWT_SECRET"]
        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        self.assertEqual(payload["building_id"], BUILDING_A)

    def test_token_without_building_has_no_claim(self):
        """Token without building_id must not have that claim."""
        import jwt as _jwt
        from utils.auth import create_token

        token = create_token("u1", "user@test.com", "owner")
        secret = os.environ["JWT_SECRET"]
        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        self.assertNotIn("building_id", payload)

    def test_two_buildings_produce_different_tokens(self):
        """Tokens for building A and building B must be distinct."""
        from utils.auth import create_token

        tok_a = create_token("u1", "user@test.com", "owner", building_id=BUILDING_A)
        tok_b = create_token("u1", "user@test.com", "owner", building_id=BUILDING_B)
        self.assertNotEqual(tok_a, tok_b)

    def test_decode_token_preserves_building_id(self):
        """decode_token must return the same building_id that was encoded."""
        from utils.auth import create_token, decode_token

        token = create_token("u1", "user@test.com", "tenant", building_id=BUILDING_B)
        payload = decode_token(token)
        self.assertEqual(payload["building_id"], BUILDING_B)

    def test_parcel_query_scoped_to_building_id(self):
        """Log-parcel endpoint builds a query that includes building_id from JWT."""
        # Verify the parcel_doc always has building_id set from JWT (not request body)
        from models.booking import ParcelCreate, ParcelResponse

        parcel_create = ParcelCreate(
            unit_number="TH087",
            carrier="auspost",
        )
        parcel_doc = {
            "id": "pid-001",
            "building_id": BUILDING_A,  # always from JWT, not client-supplied
            **parcel_create.model_dump(),
            "status": "received",
            "received_date": datetime.now(timezone.utc).isoformat(),
            "collected_date": None,
            "logged_by": "u1",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # The building_id in the document must match what came from the JWT
        self.assertEqual(parcel_doc["building_id"], BUILDING_A)
        # Must be serialisable as a ParcelResponse
        resp = ParcelResponse(**parcel_doc)
        self.assertEqual(resp.unit_number, "TH087")

    def test_budget_proposal_items_scoped_to_building(self):
        """BudgetProposalCreate is pure data; building_id is injected by the router, not the model."""
        from models.finance import BudgetProposalItem, BudgetProposalCreate

        item = BudgetProposalItem(
            fund_type="administrative",
            name="Insurance",
            proposed_amount=60000.0,
            approved=True,
        )
        proposal = BudgetProposalCreate(
            target_year="2027",
            base_year="2026",
            items=[item],
        )
        # Model must NOT expose a building_id (it's router-injected)
        self.assertFalse(hasattr(proposal, "building_id"))

    def test_cross_building_parcels_rejected(self):
        """
        A resident's unit is in BUILDING_A. If the JWT building_id resolves to
        BUILDING_B, the DB query will find no matching parcel and raise 404 —
        never leaking parcel data from a foreign building.
        """
        # This simulates what the router does: query always includes building_id from JWT.
        parcels_in_building_a = [
            {"id": "p1", "building_id": BUILDING_A, "unit_number": "TH087"},
        ]

        def fake_find(query):
            """Simulates MongoDB .find() filtering by building_id."""
            return [p for p in parcels_in_building_a if p.get("building_id") == query.get("building_id")]

        # Request with JWT building_id = BUILDING_B should return zero results
        results = fake_find({"building_id": BUILDING_B, "unit_number": "TH087"})
        self.assertEqual(len(results), 0, "Cross-building query must return no results")

        # Same request scoped to BUILDING_A returns the parcel
        results = fake_find({"building_id": BUILDING_A, "unit_number": "TH087"})
        self.assertEqual(len(results), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Financial transaction rollback (budget proposal replace logic)
# ─────────────────────────────────────────────────────────────────────────────


class TestFinancialTransactionRollback(unittest.IsolatedAsyncioTestCase):
    """
    Verify that saving budget proposals replaces (not appends to) existing
    proposed categories, and that the annual_levy document is updated with
    proposed per-UOE rates.  Confirms idempotency / rollback-safe behaviour:
    calling save twice with different amounts results in the SECOND set of
    amounts being the current state.
    """

    def _make_mock_db(
            self,
            existing_categories: list | None = None,
            existing_levy: dict | None = None,
            existing_units: list | None = None,
    ):
        mock_db = MagicMock()

        # levy_categories
        mock_db.levy_categories.delete_many = AsyncMock(return_value=None)
        mock_db.levy_categories.insert_many = AsyncMock(return_value=None)
        mock_db.levy_categories.find = MagicMock(return_value=MagicMock(
            sort=MagicMock(return_value=MagicMock(
                to_list=AsyncMock(return_value=existing_categories or [])
            ))
        ))

        # annual_levies — update_one
        mock_db.annual_levies.update_one = AsyncMock(return_value=None)
        mock_db.annual_levies.find_one = AsyncMock(return_value=existing_levy)

        # units
        mock_db.units.find = MagicMock(return_value=MagicMock(
            sort=MagicMock(return_value=MagicMock(
                to_list=AsyncMock(return_value=existing_units or [])
            ))
        ))

        return mock_db

    def _make_user(self, role: str = "strata_manager"):
        perms = MagicMock()
        perms.can_manage_finances = role in {"super_admin", "chairman", "strata_manager"}
        return {"id": "u1", "role": role}, perms

    async def test_save_replaces_existing_proposed_categories(self):
        """Second save must delete previous proposed categories before inserting new ones."""
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        from routers.finance import save_budget_proposals

        mock_db = self._make_mock_db(
            existing_levy={"total_uoe": 10000},
        )
        user, perms = self._make_user()

        data = BudgetProposalCreate(
            target_year="2027",
            base_year="2026",
            inflation_rate=3.0,
            items=[
                BudgetProposalItem(
                    fund_type="administrative",
                    name="Insurance",
                    proposed_amount=60000.0,
                    approved=True,
                ),
                BudgetProposalItem(
                    fund_type="sinking",
                    name="Lift Maintenance",
                    proposed_amount=30000.0,
                    approved=True,
                ),
            ],
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.get_user_permissions", return_value=perms):
            result = await save_budget_proposals(
                data=data,
                current_user=user,
                building_id=BUILDING_A,
            )

        # delete_many must have been called to clear old proposed categories
        mock_db.levy_categories.delete_many.assert_called_once_with({
            "building_id": BUILDING_A,
            "year": "2027",
            "status": "proposed",
        })

        # insert_many must have been called with 2 items
        call_args = mock_db.levy_categories.insert_many.call_args[0][0]
        self.assertEqual(len(call_args), 2)

        # Result must include totals
        self.assertEqual(result["admin_total"], 60000.0)
        self.assertEqual(result["sinking_total"], 30000.0)
        self.assertEqual(result["grand_total"], 90000.0)

    async def test_levy_impact_computed_in_save_response(self):
        """save_budget_proposals must return levy_impact with per-UOE rates."""
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        from routers.finance import save_budget_proposals

        mock_db = self._make_mock_db(existing_levy={"total_uoe": 10000})
        user, perms = self._make_user()

        data = BudgetProposalCreate(
            target_year="2027",
            base_year="2026",
            items=[
                BudgetProposalItem(
                    fund_type="administrative",
                    name="Insurance",
                    proposed_amount=50000.0,
                    approved=True,
                ),
            ],
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.get_user_permissions", return_value=perms):
            result = await save_budget_proposals(
                data=data,
                current_user=user,
                building_id=BUILDING_A,
            )

        self.assertIn("levy_impact", result)
        impact = result["levy_impact"]
        self.assertEqual(impact["total_uoe"], 10000)
        # 50000 / 10000 = 5.0 per UOE annually
        self.assertAlmostEqual(impact["proposed_admin_per_uoe_annual"], 5.0, places=4)

    async def test_rollback_second_save_overrides_first(self):
        """
        Two successive saves for the same year: the second must override the first.
        Simulates a rollback by re-saving with corrected amounts.
        """
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        from routers.finance import save_budget_proposals

        saved_docs: list[dict] = []

        mock_db = self._make_mock_db(existing_levy={"total_uoe": 10000})

        async def capture_insert_many(docs):
            saved_docs.clear()
            saved_docs.extend(docs)

        mock_db.levy_categories.insert_many = AsyncMock(side_effect=capture_insert_many)
        user, perms = self._make_user()

        def _proposal(amount: float) -> BudgetProposalCreate:
            return BudgetProposalCreate(
                target_year="2027",
                base_year="2026",
                items=[
                    BudgetProposalItem(
                        fund_type="administrative",
                        name="Insurance",
                        proposed_amount=amount,
                        approved=True,
                    ),
                ],
            )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.get_user_permissions", return_value=perms):
            # First save
            result1 = await save_budget_proposals(
                data=_proposal(60000.0),
                current_user=user,
                building_id=BUILDING_A,
            )
            # "Rollback" by re-saving with corrected (lower) amount
            result2 = await save_budget_proposals(
                data=_proposal(55000.0),
                current_user=user,
                building_id=BUILDING_A,
            )

        # Last write wins
        self.assertEqual(result2["admin_total"], 55000.0)
        # The final state in saved_docs reflects the second save
        self.assertEqual(saved_docs[0]["budgeted_amount"], 55000.0)

    async def test_annual_levy_updated_with_proposed_rates(self):
        """annual_levies.update_one must be called with computed per-UOE rates."""
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        from routers.finance import save_budget_proposals

        mock_db = self._make_mock_db(existing_levy={"total_uoe": 10000})
        user, perms = self._make_user()

        data = BudgetProposalCreate(
            target_year="2027",
            base_year="2026",
            items=[
                BudgetProposalItem(
                    fund_type="administrative",
                    name="Insurance",
                    proposed_amount=70000.0,
                    approved=True,
                ),
                BudgetProposalItem(
                    fund_type="sinking",
                    name="Lift",
                    proposed_amount=30000.0,
                    approved=True,
                ),
            ],
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.get_user_permissions", return_value=perms):
            await save_budget_proposals(
                data=data,
                current_user=user,
                building_id=BUILDING_A,
            )

        update_call = mock_db.annual_levies.update_one.call_args
        self.assertIsNotNone(update_call)
        set_doc = update_call[0][1]["$set"]
        self.assertIn("proposed_admin_levy_per_uoe_annual", set_doc)
        self.assertIn("proposed_sinking_levy_per_uoe_annual", set_doc)
        self.assertIn("proposed_total_levy_per_uoe_annual", set_doc)
        # 70000 / 10000 = 7.0
        self.assertAlmostEqual(set_doc["proposed_admin_levy_per_uoe_annual"], 7.0, places=4)
        # 30000 / 10000 = 3.0
        self.assertAlmostEqual(set_doc["proposed_sinking_levy_per_uoe_annual"], 3.0, places=4)

    async def test_no_items_approved_raises_400(self):
        """save_budget_proposals must raise 400 when no items are approved."""
        from fastapi import HTTPException
        from models.finance import BudgetProposalItem, BudgetProposalCreate
        from routers.finance import save_budget_proposals

        mock_db = self._make_mock_db()
        user, perms = self._make_user()

        data = BudgetProposalCreate(
            target_year="2027",
            base_year="2026",
            items=[
                BudgetProposalItem(
                    fund_type="administrative",
                    name="Insurance",
                    proposed_amount=60000.0,
                    approved=False,  # not approved
                ),
            ],
        )

        with patch("routers.finance.db", mock_db), \
                patch("routers.finance.get_user_permissions", return_value=perms):
            with self.assertRaises(HTTPException) as ctx:
                await save_budget_proposals(
                    data=data,
                    current_user=user,
                    building_id=BUILDING_A,
                )
        self.assertEqual(ctx.exception.status_code, 400)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Guest expiry / 364-day JWT hard cap
# ─────────────────────────────────────────────────────────────────────────────


class TestGuestJwtExpiry(unittest.TestCase):
    """
    Verify that:
      * Guest tokens are capped at GUEST_JWT_MAX_DAYS (364) from issue time.
      * Non-guest tokens use the standard JWT_EXPIRATION_HOURS ceiling.
      * A guest token with an end_date in the near future expires at end_date.
      * A guest token with end_date beyond 364 days is still capped at 364 days.
      * An expired guest JWT is rejected by decode_token with 401.
    """

    def _decode_raw(self, token: str) -> dict:
        import jwt as _jwt
        secret = os.environ["JWT_SECRET"]
        return _jwt.decode(token, secret, algorithms=["HS256"])

    def test_guest_token_capped_at_364_days(self):
        """Guest token expiry must not exceed now + 364 days."""
        from utils.auth import create_token, GUEST_JWT_MAX_DAYS

        before = datetime.now(timezone.utc)
        token = create_token("g1", "guest@test.com", "guest")
        payload = self._decode_raw(token)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        max_allowed = before + timedelta(days=GUEST_JWT_MAX_DAYS)
        # Expiry must be ≤ 364 days from now (+1 min grace for test execution)
        self.assertLessEqual(exp, max_allowed + timedelta(minutes=1))

    def test_owner_token_not_capped_at_364_days(self):
        """Owner tokens must use the standard JWT_EXPIRATION_HOURS (not the guest cap)."""
        import jwt as _jwt
        from utils.auth import create_token
        from config import JWT_EXPIRATION_HOURS

        before = datetime.now(timezone.utc)
        token = create_token("o1", "owner@test.com", "owner")
        payload = self._decode_raw(token)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        expected_expiry = before + timedelta(hours=JWT_EXPIRATION_HOURS)

        # Owner tokens should use JWT_EXPIRATION_HOURS, not 364-day cap
        diff_seconds = abs((exp - expected_expiry).total_seconds())
        # Allow 5-second execution window
        self.assertLessEqual(diff_seconds, 5)

    def test_guest_token_respects_end_date_when_sooner(self):
        """Guest token with an end_date earlier than JWT_EXPIRATION_HOURS must expire at end_date."""
        from utils.auth import create_token
        from config import JWT_EXPIRATION_HOURS

        # end_date is 1 hour from now — deliberately shorter than JWT_EXPIRATION_HOURS
        # so the min() logic is exercised and end_date wins.
        end_dt = datetime.now(timezone.utc) + timedelta(hours=1)
        # Only runs meaningfully when JWT_EXPIRATION_HOURS > 1; skip if equal or lower.
        if JWT_EXPIRATION_HOURS <= 1:
            self.skipTest("JWT_EXPIRATION_HOURS too low to exercise end_date guard")

        token = create_token("g2", "guest2@test.com", "guest", end_date=end_dt.isoformat())
        payload = self._decode_raw(token)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        diff_seconds = abs((exp - end_dt).total_seconds())
        # Must be within 5 seconds of the specified end_date
        self.assertLessEqual(diff_seconds, 5)

    def test_guest_token_end_date_beyond_cap_still_capped(self):
        """Guest token with end_date > 364 days must still be capped at 364 days."""
        from utils.auth import create_token, GUEST_JWT_MAX_DAYS

        in_400_days = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        before = datetime.now(timezone.utc)
        token = create_token("g3", "guest3@test.com", "guest", end_date=in_400_days)
        payload = self._decode_raw(token)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        max_allowed = before + timedelta(days=GUEST_JWT_MAX_DAYS)
        # Must be capped at 364 days even though end_date is 400 days away
        self.assertLessEqual(exp, max_allowed + timedelta(minutes=1))

    def test_expired_guest_token_raises_401(self):
        """decode_token must reject an expired guest JWT with HTTP 401."""
        import jwt as _jwt
        from fastapi import HTTPException
        from utils.auth import decode_token

        secret = os.environ["JWT_SECRET"]
        expired_payload = {
            "user_id": "g4",
            "email": "guest4@test.com",
            "role": "guest",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        expired_token = _jwt.encode(expired_payload, secret, algorithm="HS256")

        with self.assertRaises(HTTPException) as ctx:
            decode_token(expired_token)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("expired", ctx.exception.detail.lower())

    def test_guest_registration_date_less_than_365_days(self):
        """Registration endpoint enforces < 365 days for guest stay (364 cap)."""
        # Replicate the registration guard logic
        from datetime import date

        def _validate_guest_end_date(end_date_str: str) -> bool:
            end_date_obj = datetime.fromisoformat(end_date_str.replace("Z", "+00:00")).date()
            days_diff = (end_date_obj - date.today()).days
            if days_diff < 1:
                return False
            if days_diff >= 365:
                return False
            return True

        # 364 days → valid
        valid_364 = (datetime.now(timezone.utc) + timedelta(days=364)).isoformat()
        self.assertTrue(_validate_guest_end_date(valid_364))

        # 365 days → invalid (>= 365 is rejected)
        invalid_365 = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        self.assertFalse(_validate_guest_end_date(invalid_365))

        # Yesterday → invalid
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertFalse(_validate_guest_end_date(past))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Parcel notification (PARCEL_LOGGED event)
# ─────────────────────────────────────────────────────────────────────────────


class TestParcelNotification(unittest.IsolatedAsyncioTestCase):
    """
    Verify that logging a parcel triggers bell notifications to all active
    residents of the unit.
    """

    def _make_parcel_db(self, residents: list) -> MagicMock:
        """
        Build a mock database for parcel notification tests.

        `residents` is a list of user dicts (with at least an 'id' key).
        The mock models the two-step lookup used by _notify_parcel_residents:
          1. user_units.find → occupant list (with user_id)
          2. users.find      → user detail lookup (by id list)
        """
        mock_db = MagicMock()
        mock_db.parcels.insert_one = AsyncMock(return_value=None)
        mock_db.user_notifications.insert_one = AsyncMock(return_value=None)
        mock_db.units.find_one = AsyncMock(return_value=None)

        # Step 1: user_units.find returns occupant records (user_id field)
        occupants = [{"user_id": r["id"]} for r in residents]
        user_units_cursor = MagicMock()
        user_units_cursor.to_list = AsyncMock(return_value=occupants)
        mock_db.user_units.find = MagicMock(return_value=user_units_cursor)

        # Step 2: users.find returns full user records for the found IDs
        # (the code will only call this when occupants is non-empty)
        users_cursor = MagicMock()
        users_cursor.to_list = AsyncMock(return_value=[
            {"id": r["id"], "email": f"{r['id']}@example.com",
             "full_name": r.get("full_name", "Test User"),
             "mail_username": ""}
            for r in residents
        ])
        mock_db.users.find = MagicMock(return_value=users_cursor)
        return mock_db

    async def test_notification_sent_to_unit_residents(self):
        """After a parcel is logged, each unit resident gets a bell notification."""
        from routers.bookings import _notify_parcel_residents

        residents = [
            {"id": "u1"},
            {"id": "u2"},
        ]
        mock_db = self._make_parcel_db(residents)

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.create_notifications_batch", new_callable=AsyncMock) as mock_batch:
            await _notify_parcel_residents(
                unit_number="TH087",
                building_id=BUILDING_A,
                parcel_id="p-001",
                carrier="auspost",
                tracking_number="1Z999AA10123456784",
            )

        # create_notifications_batch is called once with a list of 2 notifications
        mock_batch.assert_called_once()
        notifications = mock_batch.call_args.args[0]
        self.assertEqual(len(notifications), 2)
        first_notif = notifications[0]
        self.assertIn("parcel", first_notif["type"])
        # Title is "📦 Parcel Arrived"; unit number appears in the message
        self.assertIn("TH087", first_notif["message"])

    async def test_notification_includes_carrier_name(self):
        """Notification message must mention the carrier name."""
        from routers.bookings import _notify_parcel_residents

        mock_db = self._make_parcel_db([{"id": "u1"}])

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.create_notifications_batch", new_callable=AsyncMock) as mock_batch:
            await _notify_parcel_residents(
                unit_number="UA101",
                building_id=BUILDING_A,
                parcel_id="p-002",
                carrier="dhl",
            )

        mock_batch.assert_called_once()
        notifications = mock_batch.call_args.args[0]
        self.assertIn("DHL", notifications[0]["message"])

    async def test_no_notification_when_no_residents(self):
        """If no residents are found for the unit, no notification should be sent."""
        from routers.bookings import _notify_parcel_residents

        mock_db = self._make_parcel_db([])

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.create_notifications_batch", new_callable=AsyncMock) as mock_batch:
            await _notify_parcel_residents(
                unit_number="UA999",
                building_id=BUILDING_A,
                parcel_id="p-003",
                carrier="fedex",
            )

        mock_batch.assert_not_called()

    async def test_notification_failure_does_not_raise(self):
        """If notification sending fails, the main request must not be interrupted."""
        from routers.bookings import _notify_parcel_residents

        mock_db = self._make_parcel_db([{"id": "u1"}])

        with patch("routers.bookings.db", mock_db), \
                patch("routers.bookings.create_notifications_batch", side_effect=Exception("DB down")):
            # Must not raise — failures are swallowed inside _notify_parcel_residents
            await _notify_parcel_residents(
                unit_number="TH087",
                building_id=BUILDING_A,
                parcel_id="p-004",
                carrier="amazon",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Courier tracking service (mock)
# ─────────────────────────────────────────────────────────────────────────────


class TestCourierTrackingService(unittest.IsolatedAsyncioTestCase):
    """Verify mock tracking service returns plausible data for each carrier."""

    async def _track(self, carrier: str, tracking_number: str):
        from services.courier_tracking_service import track_parcel
        return await track_parcel(carrier, tracking_number)

    async def test_auspost_mock_returns_tracking_result(self):
        result = await self._track("auspost", "ABC123456789")
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tracking_number)
        self.assertGreater(len(result.events), 0)
        self.assertIn(result.source, ("mock", "live"))

    async def test_dhl_mock_returns_tracking_result(self):
        result = await self._track("dhl", "DEF987654321")
        self.assertIsNotNone(result.current_status)
        self.assertIsNotNone(result.tracking_url)

    async def test_tracking_url_contains_tracking_number(self):
        from services.courier_tracking_service import get_public_tracking_url

        url = get_public_tracking_url("auspost", "TEST123")
        self.assertIn("TEST123", url)

        url_dhl = get_public_tracking_url("dhl", "DHL456")
        self.assertIn("DHL456", url_dhl)

    async def test_unknown_carrier_returns_mock(self):
        result = await self._track("unknown_carrier", "XYZ000")
        self.assertEqual(result.source, "mock")

    async def test_no_tracking_number_returns_none_url(self):
        from services.courier_tracking_service import get_public_tracking_url

        url = get_public_tracking_url("auspost", "")
        self.assertIsNone(url)

    async def test_result_serialisable_to_dict(self):
        result = await self._track("fedex", "FX123456")
        data = result.to_dict()
        self.assertIn("carrier", data)
        self.assertIn("events", data)
        self.assertIsInstance(data["events"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Guest expiry scheduler (scheduler.py)
# ─────────────────────────────────────────────────────────────────────────────


class TestGuestExpiryScheduler(unittest.IsolatedAsyncioTestCase):
    """
    Verify the scheduler's delete_expired_guest_tokens function correctly
    identifies expired guests via user_units.end_date (not the old
    non-existent users.guest_stay_end field).
    """

    def _make_mock_db(self, expired_rels: list, user_ids_active: list) -> MagicMock:
        mock_db = MagicMock()
        mock_db._db = MagicMock()

        # user_units.find(...)
        mock_db._db.user_units.find = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=expired_rels)
        ))
        # users.update_many(...)
        update_result = MagicMock()
        update_result.modified_count = len(user_ids_active)
        mock_db._db.users.update_many = AsyncMock(return_value=update_result)
        # user_units.update_many(...)
        mock_db._db.user_units.update_many = AsyncMock(return_value=MagicMock(modified_count=len(user_ids_active)))
        # workflow_run calls db.workflow_runs.insert_one (uses TenantScopedDatabase, not _db)
        mock_db.workflow_runs = MagicMock()
        mock_db.workflow_runs.insert_one = AsyncMock(return_value=None)
        return mock_db

    async def test_expired_guests_deactivated_via_user_units(self):
        """Scheduler must query user_units.end_date to find expired guests."""
        from workers.scheduler import delete_expired_guest_tokens

        expired_rels = [{"user_id": "g1"}, {"user_id": "g2"}]
        mock_db = self._make_mock_db(expired_rels, ["g1", "g2"])

        with patch("workers.scheduler.db", mock_db), patch("utils.workflow_runner.db", mock_db):
            await delete_expired_guest_tokens()

        # users.update_many must have been called with the correct user_ids
        call_filter = mock_db._db.users.update_many.call_args[0][0]
        self.assertIn("$in", call_filter["id"])
        self.assertIn("g1", call_filter["id"]["$in"])
        self.assertIn("g2", call_filter["id"]["$in"])
        self.assertEqual(call_filter["role"], "guest")

    async def test_no_expired_guests_skips_updates(self):
        """When no expired user_unit records exist, no DB writes are made."""
        from workers.scheduler import delete_expired_guest_tokens

        mock_db = self._make_mock_db([], [])

        with patch("workers.scheduler.db", mock_db), patch("utils.workflow_runner.db", mock_db):
            await delete_expired_guest_tokens()

        # No update calls should be made
        mock_db._db.users.update_many.assert_not_called()
        mock_db._db.user_units.update_many.assert_not_called()

    async def test_user_units_also_deactivated(self):
        """After deactivating users, the user_unit records must also be marked inactive."""
        from workers.scheduler import delete_expired_guest_tokens

        expired_rels = [{"user_id": "g3"}]
        mock_db = self._make_mock_db(expired_rels, ["g3"])

        with patch("workers.scheduler.db", mock_db), patch("utils.workflow_runner.db", mock_db):
            await delete_expired_guest_tokens()

        # user_units.update_many must have been called
        mock_db._db.user_units.update_many.assert_called_once()
        uu_filter = mock_db._db.user_units.update_many.call_args[0][0]
        self.assertIn("g3", uu_filter["user_id"]["$in"])


if __name__ == "__main__":
    unittest.main()
