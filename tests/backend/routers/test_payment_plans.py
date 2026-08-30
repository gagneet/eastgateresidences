"""Tests for routers/payment_plans.py — GAP-JUR-NSW-002.

NSW Form 1 Payment Plan lifecycle:
  POST   /payment-plans                    — create plan request
  GET    /payment-plans                    — list (manager=all, owner=own)
  GET    /payment-plans/{id}               — get single plan
  POST   /payment-plans/{id}/approve       — manager approve
  POST   /payment-plans/{id}/decline       — manager decline
  PUT    /payment-plans/{id}/status        — update status (active/completed/cancelled)
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId


class _AsyncIter:
    def __init__(self, items):
        self._it = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


BUILDING_A = "16244"
BUILDING_B = "13195"

_MANAGER = {"id": "mgr-1", "role": "strata_manager", "full_name": "Test Manager"}
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}
_EC = {"id": "ec-1", "role": "ec_member", "full_name": "EC Member"}

_OID = ObjectId()
_PLAN_UUID = str(uuid.uuid4())

_PLAN_DOC = {
    "_id": _OID,
    "id": _PLAN_UUID,
    "building_id": BUILDING_A,
    "unit_number": "10",
    "owner_id": "own-1",
    "owner_name": "Test Owner",
    "outstanding_amount_cents": 250000,
    "requested_instalment_cents": 50000,
    "instalment_frequency": "monthly",
    "proposed_start_date": "2026-05-01",
    "hardship_reason": "Temporary financial hardship due to job loss.",
    "notes": None,
    "status": "requested",
    "response_due_by": None,
    "approved_instalment_cents": None,
    "instalment_count": None,
    "start_date": None,
    "approved_by": None,
    "approved_at": None,
    "declined_by": None,
    "declined_at": None,
    "decline_reason": None,
    "submitted_by": "own-1",
    "created_at": "2026-04-23T00:00:00+00:00",
    "updated_at": "2026-04-23T00:00:00+00:00",
}


def _make_db(find_one_doc=None, count=0, list_docs=None):
    db = MagicMock()
    db.payment_plans.insert_one = AsyncMock(return_value=MagicMock(inserted_id=_OID))
    db.payment_plans.find_one = AsyncMock(return_value=find_one_doc)
    db.payment_plans.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.payment_plans.count_documents = AsyncMock(return_value=count)

    cursor = MagicMock()
    cursor.__aiter__ = lambda self=None: _AsyncIter(list_docs or [])
    db.payment_plans.find = MagicMock(return_value=MagicMock(
        sort=MagicMock(return_value=MagicMock(
            skip=MagicMock(return_value=MagicMock(limit=MagicMock(return_value=cursor)))
        ))
    ))
    return db


# ── Create endpoint ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_plan_returns_uuid_id():
    """After create, the returned doc has a UUID 'id' field (not an ObjectId)."""
    from routers.payment_plans import create_payment_plan, PaymentPlanCreate
    db = _make_db()
    payload = PaymentPlanCreate(
        unit_number="10",
        outstanding_amount_cents=250000,
        requested_instalment_cents=50000,
        instalment_frequency="monthly",
        proposed_start_date="2026-05-01",
    )
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        result = await create_payment_plan(
            payload=payload, building_id=BUILDING_A, current_user=_OWNER
        )
    plan_id = result.get("id")
    assert plan_id is not None
    # Must be a valid UUID string, not an ObjectId hex
    assert len(plan_id) == 36, f"Expected UUID (36 chars), got: {plan_id!r}"
    try:
        uuid.UUID(plan_id)
    except ValueError:
        pytest.fail(f"'id' field is not a valid UUID: {plan_id!r}")
    # _clean() must strip _id but keep 'id'
    assert "_id" not in result


@pytest.mark.asyncio
async def test_create_plan_sets_building_id():
    """Inserted document must carry the correct building_id."""
    from routers.payment_plans import create_payment_plan, PaymentPlanCreate
    db = _make_db()
    payload = PaymentPlanCreate(
        unit_number="10",
        outstanding_amount_cents=250000,
        requested_instalment_cents=50000,
        instalment_frequency="monthly",
        proposed_start_date="2026-05-01",
    )
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        await create_payment_plan(
            payload=payload, building_id=BUILDING_A, current_user=_OWNER
        )
    inserted = db.payment_plans.insert_one.call_args[0][0]
    assert inserted["building_id"] == BUILDING_A
    assert inserted["owner_id"] == _OWNER["id"]
    assert inserted["status"] == "requested"


@pytest.mark.asyncio
async def test_create_plan_invalid_frequency_400():
    """An invalid instalment_frequency must raise HTTP 400."""
    from routers.payment_plans import create_payment_plan, PaymentPlanCreate
    from fastapi import HTTPException
    db = _make_db()
    payload = PaymentPlanCreate(
        unit_number="10",
        outstanding_amount_cents=250000,
        requested_instalment_cents=50000,
        instalment_frequency="daily",  # not valid
        proposed_start_date="2026-05-01",
    )
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc:
            await create_payment_plan(
                payload=payload, building_id=BUILDING_A, current_user=_OWNER
            )
    assert exc.value.status_code == 400


# ── List endpoint ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plans_manager_sees_all():
    """A manager's list query must NOT include an owner_id filter."""
    from routers.payment_plans import list_payment_plans
    db = _make_db(count=0, list_docs=[])
    with patch("routers.payment_plans._get_db", return_value=db):
        await list_payment_plans(
            status=None, unit_number=None, page=1, page_size=20,
            building_id=BUILDING_A, current_user=_MANAGER,
        )
    query = db.payment_plans.count_documents.call_args[0][0]
    assert "owner_id" not in query
    assert query["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_list_plans_owner_sees_only_own():
    """An owner's list query must include owner_id == owner's id."""
    from routers.payment_plans import list_payment_plans
    db = _make_db(count=0, list_docs=[])
    with patch("routers.payment_plans._get_db", return_value=db):
        await list_payment_plans(
            status=None, unit_number=None, page=1, page_size=20,
            building_id=BUILDING_A, current_user=_OWNER,
        )
    query = db.payment_plans.count_documents.call_args[0][0]
    assert query.get("owner_id") == _OWNER["id"]
    assert query["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_list_plans_status_filter():
    """A valid status filter must be added to the query."""
    from routers.payment_plans import list_payment_plans
    db = _make_db(count=0, list_docs=[])
    with patch("routers.payment_plans._get_db", return_value=db):
        await list_payment_plans(
            status="approved", unit_number=None, page=1, page_size=20,
            building_id=BUILDING_A, current_user=_MANAGER,
        )
    query = db.payment_plans.count_documents.call_args[0][0]
    assert query["status"] == "approved"


@pytest.mark.asyncio
async def test_list_plans_invalid_status_400():
    """An invalid status filter must raise HTTP 400."""
    from routers.payment_plans import list_payment_plans
    from fastapi import HTTPException
    db = _make_db(count=0, list_docs=[])
    with patch("routers.payment_plans._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await list_payment_plans(
                status="nonexistent", unit_number=None, page=1, page_size=20,
                building_id=BUILDING_A, current_user=_MANAGER,
            )
    assert exc.value.status_code == 400


# ── Get single plan ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_plan_not_found_404():
    """get_payment_plan must raise 404 when plan is not found."""
    from routers.payment_plans import get_payment_plan
    from fastapi import HTTPException
    db = _make_db(find_one_doc=None)
    with patch("routers.payment_plans._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await get_payment_plan(
                plan_id="nonexistent-id", building_id=BUILDING_A, current_user=_MANAGER
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_plan_owner_cannot_see_other_owner():
    """_can_view returns False for wrong owner → 403."""
    from routers.payment_plans import get_payment_plan
    from fastapi import HTTPException
    # Plan belongs to "own-1", but a different owner is requesting it
    other_owner = {"id": "own-999", "role": "owner", "full_name": "Other Owner"}
    doc_for_own1 = {**_PLAN_DOC, "owner_id": "own-1"}
    db = _make_db(find_one_doc=doc_for_own1)
    with patch("routers.payment_plans._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await get_payment_plan(
                plan_id=_PLAN_UUID, building_id=BUILDING_A, current_user=other_owner
            )
    assert exc.value.status_code == 403


# ── Approve endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_requires_manager():
    """An owner calling approve must receive HTTP 403."""
    from routers.payment_plans import approve_payment_plan, PaymentPlanApprove
    from fastapi import HTTPException
    payload = PaymentPlanApprove(
        approved_instalment_cents=50000,
        instalment_frequency="monthly",
        instalment_count=5,
        start_date="2026-05-01",
    )
    with pytest.raises(HTTPException) as exc:
        await approve_payment_plan(
            plan_id=_PLAN_UUID, payload=payload, building_id=BUILDING_A, current_user=_OWNER
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_approve_only_from_requested_status():
    """Approving a plan that's already 'approved' must raise 409."""
    from routers.payment_plans import approve_payment_plan, PaymentPlanApprove
    from fastapi import HTTPException
    already_approved = {**_PLAN_DOC, "status": "approved"}
    db = _make_db(find_one_doc=already_approved)
    payload = PaymentPlanApprove(
        approved_instalment_cents=50000,
        instalment_frequency="monthly",
        instalment_count=5,
        start_date="2026-05-01",
    )
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc:
            await approve_payment_plan(
                plan_id=_PLAN_UUID, payload=payload,
                building_id=BUILDING_A, current_user=_MANAGER
            )
    assert exc.value.status_code == 409


# ── Decline endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decline_requires_manager():
    """An owner calling decline must receive HTTP 403."""
    from routers.payment_plans import decline_payment_plan, PaymentPlanDecline
    from fastapi import HTTPException
    payload = PaymentPlanDecline(reason="Insufficient documentation provided for the request.")
    with pytest.raises(HTTPException) as exc:
        await decline_payment_plan(
            plan_id=_PLAN_UUID, payload=payload, building_id=BUILDING_A, current_user=_OWNER
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_decline_only_from_requested_status():
    """Declining an already-declined plan must raise 409."""
    from routers.payment_plans import decline_payment_plan, PaymentPlanDecline
    from fastapi import HTTPException
    already_declined = {**_PLAN_DOC, "status": "declined"}
    db = _make_db(find_one_doc=already_declined)
    payload = PaymentPlanDecline(reason="Insufficient documentation provided for the request.")
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc:
            await decline_payment_plan(
                plan_id=_PLAN_UUID, payload=payload,
                building_id=BUILDING_A, current_user=_MANAGER
            )
    assert exc.value.status_code == 409


# ── Status update endpoint ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_update_blocked_on_terminal():
    """update_plan_status must raise 409 when plan is already in a terminal status."""
    from routers.payment_plans import update_plan_status, PaymentPlanStatusUpdate
    from fastapi import HTTPException
    for terminal_status in ("declined", "completed", "cancelled"):
        terminal_plan = {**_PLAN_DOC, "status": terminal_status}
        db = _make_db(find_one_doc=terminal_plan)
        payload = PaymentPlanStatusUpdate(status="active")
        with patch("routers.payment_plans._get_db", return_value=db), \
                patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_plan_status(
                    plan_id=_PLAN_UUID, payload=payload,
                    building_id=BUILDING_A, current_user=_MANAGER
                )
        assert exc.value.status_code == 409, (
            f"Expected 409 for terminal status '{terminal_status}', got {exc.value.status_code}"
        )


# ── Multi-tenant isolation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_building_isolation():
    """A plan seeded under BUILDING_A must not be reachable from BUILDING_B."""
    from routers.payment_plans import get_payment_plan
    from fastapi import HTTPException
    # DB returns None when the building_id filter is BUILDING_B (simulates no match)
    db = _make_db(find_one_doc=None)
    with patch("routers.payment_plans._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await get_payment_plan(
                plan_id=_PLAN_UUID, building_id=BUILDING_B, current_user=_MANAGER
            )
    assert exc.value.status_code == 404
    # Verify the query included BUILDING_B's id (not BUILDING_A's)
    query = db.payment_plans.find_one.call_args[0][0]
    assert query["building_id"] == BUILDING_B


@pytest.mark.asyncio
async def test_list_plans_scoped_to_building():
    """List query must always include building_id — never returns cross-building data."""
    from routers.payment_plans import list_payment_plans
    db = _make_db(count=0, list_docs=[])
    with patch("routers.payment_plans._get_db", return_value=db):
        await list_payment_plans(
            status=None, unit_number=None, page=1, page_size=20,
            building_id=BUILDING_B, current_user=_MANAGER,
        )
    query = db.payment_plans.count_documents.call_args[0][0]
    assert query["building_id"] == BUILDING_B


@pytest.mark.asyncio
async def test_approve_sets_approved_fields():
    """After approval, the update payload must include status=approved and approved_by."""
    from routers.payment_plans import approve_payment_plan, PaymentPlanApprove
    requested_plan = {**_PLAN_DOC, "status": "requested"}
    db = _make_db(find_one_doc=requested_plan)
    payload = PaymentPlanApprove(
        approved_instalment_cents=50000,
        instalment_frequency="monthly",
        instalment_count=5,
        start_date="2026-05-01",
    )
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        result = await approve_payment_plan(
            plan_id=_PLAN_UUID, payload=payload,
            building_id=BUILDING_A, current_user=_MANAGER
        )
    update_call = db.payment_plans.update_one.call_args[0][1]
    assert update_call["$set"]["status"] == "approved"
    assert update_call["$set"]["approved_by"] == _MANAGER["id"]
    assert "approved_at" in update_call["$set"]
    assert result["plan_id"] == _PLAN_UUID


@pytest.mark.asyncio
async def test_decline_sets_declined_fields():
    """After decline, the update payload must include status=declined and decline_reason."""
    from routers.payment_plans import decline_payment_plan, PaymentPlanDecline
    requested_plan = {**_PLAN_DOC, "status": "requested"}
    db = _make_db(find_one_doc=requested_plan)
    reason_text = "Insufficient documentation provided for the request."
    payload = PaymentPlanDecline(reason=reason_text)
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        result = await decline_payment_plan(
            plan_id=_PLAN_UUID, payload=payload,
            building_id=BUILDING_A, current_user=_MANAGER
        )
    update_call = db.payment_plans.update_one.call_args[0][1]
    assert update_call["$set"]["status"] == "declined"
    assert update_call["$set"]["decline_reason"] == reason_text
    assert update_call["$set"]["declined_by"] == _MANAGER["id"]
    assert result["plan_id"] == _PLAN_UUID


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner_to_approve():
    """effective_role='strata_manager' on a raw-owner user can approve a payment plan."""
    from routers.payment_plans import approve_payment_plan, PaymentPlanApprove
    elevated_owner = {"id": "eo-1", "role": "owner", "effective_role": "strata_manager"}
    requested_plan = {**_PLAN_DOC, "status": "requested"}
    payload = PaymentPlanApprove(
        approved_instalment_cents=25000,
        instalment_frequency="monthly",
        instalment_count=4,
        start_date="2026-05-01",
    )
    db = _make_db(find_one_doc=requested_plan)
    with patch("routers.payment_plans._get_db", return_value=db), \
            patch("routers.payment_plans.create_audit_log", new_callable=AsyncMock):
        result = await approve_payment_plan(
            plan_id=_PLAN_UUID, payload=payload,
            building_id=BUILDING_A, current_user=elevated_owner,
        )
    assert result["plan_id"] == _PLAN_UUID


@pytest.mark.asyncio
async def test_effective_role_blocks_downgraded_manager_from_approve():
    """effective_role='owner' on a raw-manager user must be blocked from approving plans."""
    from routers.payment_plans import approve_payment_plan, PaymentPlanApprove
    from fastapi import HTTPException
    downgraded = {"id": "dm-1", "role": "strata_manager", "effective_role": "owner"}
    payload = PaymentPlanApprove(
        approved_instalment_cents=25000,
        instalment_frequency="monthly",
        instalment_count=4,
        start_date="2026-05-01",
    )
    with pytest.raises(HTTPException) as exc:
        await approve_payment_plan(
            plan_id=_PLAN_UUID, payload=payload,
            building_id=BUILDING_A, current_user=downgraded,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_ec_member_can_list_all_plans():
    """EC member is a manager role — must see all plans, not just own."""
    from routers.payment_plans import list_payment_plans
    _EC = {"id": "ec-1", "role": "ec_member"}
    db = _make_db(list_docs=[], count=2)
    with patch("routers.payment_plans._get_db", return_value=db):
        result = await list_payment_plans(
            status=None, unit_number=None, page=1, page_size=20,
            building_id=BUILDING_A, current_user=_EC,
        )
    # EC member should NOT have owner_id filter applied
    count_query = db.payment_plans.count_documents.call_args[0][0]
    assert "owner_id" not in count_query
    assert result["total"] == 2
