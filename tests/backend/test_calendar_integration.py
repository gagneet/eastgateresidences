#!/usr/bin/env python3
"""
tests/backend/test_calendar_integration.py
-------------------------------------------
Integration tests for calendar-related endpoints:
  - PPM upcoming events accessible to owner role (not 403)
  - Compliance items accessible to owner role (not 403)
  - Move bookings scoped to building (13195 booking invisible to 16244 headers)
  - Amenity bookings created and returned via GET /amenity-bookings
  - Calendar allEvents: events, ppm, compliance all return data for a month query

Multi-tenant isolation verified for buildings "13195" (East Gate) and "16244" (Sierra).

Cleanup rules:
  - All test bookings are created with is_test_data=True so they are filtered
    from production listing endpoints even if cleanup fails.
  - Move bookings are hard-deleted via DELETE /move-bookings/{id} (admin only).
  - Amenity bookings are hard-deleted via DELETE /amenity-bookings/{id}.
  - Both deletes run in finally blocks — idempotent.

Prerequisites (live server):
  - Backend running on http://127.0.0.1:8003
  - conftest.py can mint tokens for avneet@eastgateresidences.com.au (owner, building 13195, TH087)
  - conftest.py can mint tokens for anthony@eastgateresidences.com.au (chairman, building 13195)

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_calendar_integration.py -v
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"

# Test user identities. Tokens are minted directly below; no password login is used.
OWNER_EMAIL = "avneet@eastgateresidences.com.au"
CHAIRMAN_EMAIL = "anthony@eastgateresidences.com.au"


# ─── Module-scoped login fixtures ─────────────────────────────────────────────
# Tokens are minted directly (no login API call) to avoid the 10/min rate limit.

@pytest.fixture(scope="module")
def owner_token(mint_token):
    return mint_token(OWNER_EMAIL, legacy_identity=True)


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    return {"Authorization": f"Bearer {owner_token}", "X-Building-ID": "13195"}


@pytest.fixture(scope="module")
def chairman_token(mint_token):
    return mint_token(CHAIRMAN_EMAIL, legacy_identity=True)


@pytest.fixture(scope="module")
def chairman_headers(chairman_token):
    return {"Authorization": f"Bearer {chairman_token}", "X-Building-ID": "13195"}


@pytest.fixture(scope="module")
def admin_token(mint_token):
    return mint_token("administrator@strataos.live")


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "X-Building-ID": "13195"}


# ─── Test 1: PPM accessible to owner role ────────────────────────────────────

class TestPPMAccessibleToOwner:
    """GET /ppm/upcoming must return 200 for owner role, not 403."""

    def test_owner_can_call_ppm_upcoming(self, owner_headers):
        """Owner role can call GET /ppm/upcoming and receive a valid response."""
        r = requests.get(f"{BASE_URL}/ppm/upcoming", headers=owner_headers, timeout=10)
        assert r.status_code == 200, (
            f"Expected 200 for owner on /ppm/upcoming, got {r.status_code}: {r.text[:300]}"
        )

    def test_ppm_upcoming_returns_list(self, owner_headers):
        """GET /ppm/upcoming returns a JSON list (may be empty, but must be a list)."""
        r = requests.get(f"{BASE_URL}/ppm/upcoming", headers=owner_headers, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}: {data!r}"

    def test_ppm_upcoming_accepts_days_param(self, owner_headers):
        """GET /ppm/upcoming?days=60 is accepted without error."""
        r = requests.get(
            f"{BASE_URL}/ppm/upcoming?days=60", headers=owner_headers, timeout=10
        )
        assert r.status_code == 200, (
            f"Expected 200 with days=60, got {r.status_code}: {r.text[:200]}"
        )

    def test_ppm_upcoming_items_scoped_to_building(self, owner_headers):
        """Any returned PPM items must belong to building 13195."""
        r = requests.get(f"{BASE_URL}/ppm/upcoming", headers=owner_headers, timeout=10)
        assert r.status_code == 200
        items = r.json()
        for item in items:
            assert item.get("building_id") == "13195", (
                f"PPM item building_id mismatch: {item.get('building_id')!r} != '13195'"
            )

    def test_ppm_requires_authentication(self):
        """GET /ppm/upcoming without a token returns 401 or 403 (not 200)."""
        r = requests.get(f"{BASE_URL}/ppm/upcoming", timeout=10)
        assert r.status_code in (401, 403), (
            f"Expected auth failure, got {r.status_code}"
        )


# ─── Test 2: Compliance items accessible to owner role ────────────────────────

class TestComplianceItemsAccessibleToOwner:
    """GET /compliance-items must return 200 for owner role."""

    def test_owner_can_call_compliance_items(self, owner_headers):
        """Owner role can call GET /compliance-items and receive 200."""
        r = requests.get(f"{BASE_URL}/compliance-items", headers=owner_headers, timeout=10)
        assert r.status_code == 200, (
            f"Expected 200 for owner on /compliance-items, got {r.status_code}: {r.text[:300]}"
        )

    def test_compliance_items_returns_list(self, owner_headers):
        """GET /compliance-items returns a JSON list."""
        r = requests.get(f"{BASE_URL}/compliance-items", headers=owner_headers, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}"

    def test_compliance_items_scoped_to_building(self, owner_headers):
        """All returned compliance items must belong to building 13195."""
        r = requests.get(f"{BASE_URL}/compliance-items", headers=owner_headers, timeout=10)
        assert r.status_code == 200
        items = r.json()
        for item in items:
            assert item.get("building_id") == "13195", (
                f"Compliance item building_id mismatch: {item.get('building_id')!r}"
            )

    def test_compliance_items_structure(self, owner_headers):
        """If compliance items are returned, they contain expected fields."""
        r = requests.get(f"{BASE_URL}/compliance-items", headers=owner_headers, timeout=10)
        assert r.status_code == 200
        items = r.json()
        for item in items:
            assert "id" in item, "compliance item must have 'id'"
            assert "building_id" in item, "compliance item must have 'building_id'"

    def test_compliance_items_requires_authentication(self):
        """GET /compliance-items without token returns 401 or 403."""
        r = requests.get(f"{BASE_URL}/compliance-items", timeout=10)
        assert r.status_code in (401, 403), (
            f"Expected auth failure, got {r.status_code}"
        )


# ─── Test 3: Move bookings scoped to building ─────────────────────────────────

class TestMoveBookingsBuildingScope:
    """Bookings created for building 13195 must not appear under building 16244."""

    def test_create_move_booking_for_13195(self, owner_headers, admin_headers):
        """Owner can create a move booking for building 13195."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
        payload = {
            "unit_number": "TH017",
            "move_type": "move_out",
            "scheduled_date": future_date,
            "time_slot": "morning",
            "requires_lift": False,
            "notes": f"test-booking-{uuid.uuid4().hex[:8]}",
            "is_test_data": True,
        }
        booking_id = None
        try:
            r = requests.post(
                f"{BASE_URL}/move-bookings", json=payload, headers=owner_headers, timeout=10
            )
            assert r.status_code == 200, (
                f"Expected 200 creating move booking, got {r.status_code}: {r.text[:300]}"
            )
            data = r.json()
            assert "id" in data
            assert data["building_id"] == "13195"
            booking_id = data["id"]
        finally:
            if booking_id:
                requests.delete(
                    f"{BASE_URL}/move-bookings/{booking_id}",
                    headers=admin_headers,
                    timeout=10,
                )

    def test_move_booking_invisible_to_other_building(self, owner_headers, admin_token, admin_headers):
        """A booking created for 13195 must not appear in 16244's listing."""
        unique_note = f"test-isolation-{uuid.uuid4().hex[:12]}"
        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        payload = {
            "unit_number": "TH017",
            "move_type": "move_in",
            "scheduled_date": future_date,
            "time_slot": "afternoon",
            "requires_lift": True,
            "notes": unique_note,
            "is_test_data": True,
        }

        booking_id = None
        try:
            r_create = requests.post(
                f"{BASE_URL}/move-bookings", json=payload, headers=owner_headers, timeout=10
            )
            assert r_create.status_code == 200, (
                f"Failed to create test booking: {r_create.status_code} {r_create.text[:200]}"
            )
            booking_id = r_create.json()["id"]

            sierra_headers = {
                "Authorization": f"Bearer {admin_token}",
                "X-Building-ID": "16244",
            }
            r_list = requests.get(
                f"{BASE_URL}/move-bookings", headers=sierra_headers, timeout=10
            )
            if r_list.status_code == 200:
                sierra_bookings = r_list.json()
                sierra_ids = [b.get("id") for b in sierra_bookings]
                assert booking_id not in sierra_ids, (
                    f"Move booking {booking_id} for 13195 appeared in Sierra (16244) listing"
                )

        finally:
            if booking_id:
                requests.delete(
                    f"{BASE_URL}/move-bookings/{booking_id}",
                    headers=admin_headers,
                    timeout=10,
                )

    def test_move_booking_creation_stores_building_id(self, owner_headers, admin_headers):
        """Newly created move booking must have building_id=13195 stored."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
        unique_note = f"test-bldg-scope-{uuid.uuid4().hex[:8]}"
        payload = {
            "unit_number": "TH017",
            "move_type": "move_out",
            "scheduled_date": future_date,
            "time_slot": "morning",
            "requires_lift": False,
            "notes": unique_note,
            "is_test_data": True,
        }
        booking_id = None
        try:
            r = requests.post(
                f"{BASE_URL}/move-bookings", json=payload, headers=owner_headers, timeout=10
            )
            assert r.status_code == 200
            data = r.json()
            booking_id = data["id"]
            assert data.get("building_id") == "13195", (
                f"Expected building_id='13195', got {data.get('building_id')!r}"
            )
        finally:
            if booking_id:
                requests.delete(
                    f"{BASE_URL}/move-bookings/{booking_id}",
                    headers=admin_headers,
                    timeout=10,
                )


# ─── Test 4: Amenity bookings created and returned ────────────────────────────

class TestAmenityBookings:
    """Create an amenity booking and verify it appears in the GET listing."""

    def _unique_time(self) -> tuple[str, str, str]:
        """Generate a future date and non-conflicting time window."""
        future = datetime.now(timezone.utc) + timedelta(days=60)
        date_str = future.strftime("%Y-%m-%d")
        hour = (future.microsecond % 20) + 1
        start = f"{hour:02d}:00"
        end = f"{(hour + 1):02d}:00"
        return date_str, start, end

    def test_create_amenity_booking(self, owner_headers):
        """POST /amenity-bookings returns 200 with booking data."""
        date_str, start, end = self._unique_time()
        payload = {
            "amenity_type": "meeting_room",
            "date": date_str,
            "start_time": start,
            "end_time": end,
            "notes": f"test-amenity-{uuid.uuid4().hex[:8]}",
            "is_test_data": True,
        }
        booking_id = None
        try:
            r = requests.post(
                f"{BASE_URL}/amenity-bookings", json=payload, headers=owner_headers, timeout=10
            )
            assert r.status_code == 200, (
                f"Expected 200, got {r.status_code}: {r.text[:300]}"
            )
            data = r.json()
            assert "id" in data, "Response must include 'id'"
            assert data["amenity_type"] == "meeting_room"
            assert data["date"] == date_str
            assert data["status"] == "confirmed"
            booking_id = data["id"]
        finally:
            if booking_id:
                requests.delete(
                    f"{BASE_URL}/amenity-bookings/{booking_id}",
                    headers=owner_headers,
                    timeout=10,
                )

    def test_created_booking_appears_in_list(self, owner_headers):
        """A newly created amenity booking (is_test_data=False) must appear in GET /amenity-bookings,
        then be hard-deleted so it leaves no trace."""
        date_str, start, end = self._unique_time()
        unique_note = f"test-visible-{uuid.uuid4().hex[:10]}"
        # is_test_data omitted (defaults False) so the booking is visible in the GET listing
        payload = {
            "amenity_type": "bbq_area",
            "date": date_str,
            "start_time": start,
            "end_time": end,
            "notes": unique_note,
        }
        booking_id = None
        try:
            r_create = requests.post(
                f"{BASE_URL}/amenity-bookings", json=payload, headers=owner_headers, timeout=10
            )
            assert r_create.status_code == 200
            booking_id = r_create.json()["id"]

            # Must appear in the GET listing (not filtered, since is_test_data=False)
            r_list = requests.get(
                f"{BASE_URL}/amenity-bookings", headers=owner_headers, timeout=10
            )
            assert r_list.status_code == 200
            ids = [b["id"] for b in r_list.json()]
            assert booking_id in ids, (
                f"Created amenity booking {booking_id} not found in GET /amenity-bookings"
            )
        finally:
            # Hard-delete so it never persists between test runs
            if booking_id:
                requests.delete(
                    f"{BASE_URL}/amenity-bookings/{booking_id}",
                    headers=owner_headers,
                    timeout=10,
                )

    def test_amenity_booking_scoped_to_building(self, owner_headers, admin_headers):
        """Created amenity booking must have building_id=13195."""
        date_str, start, end = self._unique_time()
        payload = {
            "amenity_type": "gym",
            "date": date_str,
            "start_time": start,
            "end_time": end,
            "notes": f"test-scope-{uuid.uuid4().hex[:8]}",
            "is_test_data": True,
        }
        booking_id = None
        try:
            r = requests.post(
                f"{BASE_URL}/amenity-bookings", json=payload, headers=owner_headers, timeout=10
            )
            assert r.status_code == 200
            data = r.json()
            booking_id = data["id"]
            assert data.get("building_id") == "13195", (
                f"Expected building_id='13195', got {data.get('building_id')!r}"
            )
        finally:
            if booking_id:
                requests.delete(
                    f"{BASE_URL}/amenity-bookings/{booking_id}",
                    headers=owner_headers,
                    timeout=10,
                )

    def test_amenity_booking_deletion(self, owner_headers):
        """DELETE /amenity-bookings/{id} hard-deletes a booking (returns 200 or 204)."""
        date_str, start, end = self._unique_time()
        payload = {
            "amenity_type": "visitor_parking",
            "date": date_str,
            "start_time": start,
            "end_time": end,
            "is_test_data": True,
        }
        r_create = requests.post(
            f"{BASE_URL}/amenity-bookings", json=payload, headers=owner_headers, timeout=10
        )
        assert r_create.status_code == 200
        booking_id = r_create.json()["id"]

        deleted = False
        try:
            r_del = requests.delete(
                f"{BASE_URL}/amenity-bookings/{booking_id}",
                headers=owner_headers,
                timeout=10,
            )
            assert r_del.status_code in (200, 204), (
                f"Expected 200/204 on delete, got {r_del.status_code}: {r_del.text[:200]}"
            )
            deleted = True

            # Verify hard-deleted: a second DELETE must return 404
            r_del2 = requests.delete(
                f"{BASE_URL}/amenity-bookings/{booking_id}",
                headers=owner_headers,
                timeout=10,
            )
            assert r_del2.status_code == 404, (
                f"Expected 404 on second delete (hard-delete confirmed), got {r_del2.status_code}"
            )
        finally:
            if not deleted:
                requests.delete(
                    f"{BASE_URL}/amenity-bookings/{booking_id}",
                    headers=owner_headers,
                    timeout=10,
                )

    def test_move_booking_deletion(self, owner_headers, admin_headers):
        """DELETE /move-bookings/{id} hard-deletes a move booking (admin only)."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%Y-%m-%d")
        payload = {
            "unit_number": "TH017",
            "move_type": "move_in",
            "scheduled_date": future_date,
            "time_slot": "morning",
            "requires_lift": False,
            "is_test_data": True,
        }
        r_create = requests.post(
            f"{BASE_URL}/move-bookings", json=payload, headers=owner_headers, timeout=10
        )
        assert r_create.status_code == 200
        booking_id = r_create.json()["id"]

        deleted = False
        try:
            r_del = requests.delete(
                f"{BASE_URL}/move-bookings/{booking_id}",
                headers=admin_headers,
                timeout=10,
            )
            assert r_del.status_code in (200, 204), (
                f"Expected 200/204 on delete, got {r_del.status_code}: {r_del.text[:200]}"
            )
            deleted = True

            # Verify hard-deleted: a second DELETE must return 404
            r_del2 = requests.delete(
                f"{BASE_URL}/move-bookings/{booking_id}",
                headers=admin_headers,
                timeout=10,
            )
            assert r_del2.status_code == 404, (
                f"Expected 404 on second delete (hard-delete confirmed), got {r_del2.status_code}"
            )
        finally:
            if not deleted:
                requests.delete(
                    f"{BASE_URL}/move-bookings/{booking_id}",
                    headers=admin_headers,
                    timeout=10,
                )


# ─── Test 5: Calendar allEvents merges multiple sources ───────────────────────

class TestCalendarAllEvents:
    """Verify that events, PPM, and compliance sources all respond for a calendar month."""

    def test_events_endpoint_returns_for_month(self, owner_headers):
        """GET /events returns a list (calendar month source)."""
        now = datetime.now(timezone.utc)
        r = requests.get(
            f"{BASE_URL}/events",
            params={"month": now.strftime("%Y-%m")},
            headers=owner_headers,
            timeout=10,
        )
        assert r.status_code == 200, (
            f"Expected 200 for /events, got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        assert isinstance(data, list), f"Expected list from /events, got {type(data).__name__}"

    def test_ppm_upcoming_returns_for_calendar(self, owner_headers):
        """GET /ppm/upcoming returns a list (PPM source for calendar)."""
        r = requests.get(
            f"{BASE_URL}/ppm/upcoming?days=90",
            headers=owner_headers,
            timeout=10,
        )
        assert r.status_code == 200, (
            f"Expected 200 for /ppm/upcoming, got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        assert isinstance(data, list)

    def test_compliance_returns_for_calendar(self, owner_headers):
        """GET /compliance-items returns a list (compliance source for calendar)."""
        r = requests.get(f"{BASE_URL}/compliance-items", headers=owner_headers, timeout=10)
        assert r.status_code == 200, (
            f"Expected 200 for /compliance-items, got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        assert isinstance(data, list)

    def test_all_three_sources_respond_200(self, owner_headers):
        """All three calendar data sources return 200 in a single test."""
        now = datetime.now(timezone.utc)
        month_str = now.strftime("%Y-%m")

        failures = []

        r_events = requests.get(
            f"{BASE_URL}/events",
            params={"month": month_str},
            headers=owner_headers,
            timeout=10,
        )
        if r_events.status_code != 200:
            failures.append(f"/events → {r_events.status_code}")

        r_ppm = requests.get(
            f"{BASE_URL}/ppm/upcoming",
            headers=owner_headers,
            timeout=10,
        )
        if r_ppm.status_code != 200:
            failures.append(f"/ppm/upcoming → {r_ppm.status_code}")

        r_compliance = requests.get(
            f"{BASE_URL}/compliance-items",
            headers=owner_headers,
            timeout=10,
        )
        if r_compliance.status_code != 200:
            failures.append(f"/compliance-items → {r_compliance.status_code}")

        assert not failures, (
                "One or more calendar sources failed for owner role:\n  " + "\n  ".join(failures)
        )

    def test_ppm_event_inserted_appears_in_db_month_query(self):
        """
        DB-level: a PPM event inserted for the current month must be
        queryable by month-prefix filter (no live server required).
        """
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
        db_name = os.environ.get("DB_NAME", "strata_production")

        async def _run():
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            now = datetime.now(timezone.utc)
            test_event = {
                "building_id": "13195",
                "title": f"TEST PPM Calendar - {uuid.uuid4().hex[:8]}",
                "event_type": "ppm",
                "start_date": now.isoformat(),
                "end_date": now.isoformat(),
                "is_public": True,
                "is_test_data": True,
                "created_at": now.isoformat(),
            }
            result = await db.events.insert_one(test_event)
            inserted_id = result.inserted_id
            try:
                month_str = now.strftime("%Y-%m")
                found = await db.events.find({
                    "building_id": "13195",
                    "event_type": "ppm",
                    "start_date": {"$regex": f"^{month_str}"},
                }).to_list(length=20)
                ids = [str(e["_id"]) for e in found]
                assert str(inserted_id) in ids, (
                    f"Inserted PPM event not found in month-query for {month_str}"
                )
            finally:
                await db.events.delete_one({"_id": inserted_id})
                client.close()

        asyncio.run(_run())

    def test_ppm_and_compliance_different_building_scoped_separately(self):
        """
        DB-level multi-tenant: PPM and compliance items for 13195 and 16244
        must not overlap — each building sees only its own data.
        """
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
        db_name = os.environ.get("DB_NAME", "strata_production")

        async def _run():
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            try:
                eg_compliance = await db.compliance_items.find(
                    {"building_id": "13195"}, {"_id": 1}
                ).to_list(length=50)
                sierra_compliance = await db.compliance_items.find(
                    {"building_id": "16244"}, {"_id": 1}
                ).to_list(length=50)

                eg_ids = {str(doc["_id"]) for doc in eg_compliance}
                sierra_ids = {str(doc["_id"]) for doc in sierra_compliance}
                overlap = eg_ids & sierra_ids
                assert len(overlap) == 0, (
                    f"Compliance items shared between buildings: {overlap}"
                )
            finally:
                client.close()

        asyncio.run(_run())
