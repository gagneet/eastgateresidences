"""Obligations must reach the response body, not stop at the Decision.

Before ``services/obligation_enforcement.py`` existed, ``decide()`` returned
populated obligations, ``field_masking.apply_obligations()`` was fully tested,
and no route applied either. Owner contact details, per-lot arrears and supplier
bank fields were returned in full from every capability-guarded route.

These tests pin the carrier end to end: a guarded route is called through a real
ASGI stack, and the assertion is on the bytes the client receives — not on the
Decision object, which was already correct and already ignored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.user import UserRole  # noqa: E402
from services.capability_registry import require_capability  # noqa: E402
from services.field_masking import WITHHELD  # noqa: E402
from services.obligation_enforcement import (  # noqa: E402
    ObligationEnforcementMiddleware,
    apply_pending_obligations,
    decision_ids,
    mark_obligations_applied,
    pending_obligations,
    record_obligations,
)

BUILDING = "TESTBLD1"


def _user(role: str, **extra) -> dict:
    """A subject with a verified building assignment, shaped as auth puts it on the request."""
    return {
        "id": "user-1",
        "email": "someone@example.com",
        "role": role,
        "building_id": BUILDING,
        "building_ids": [BUILDING],
        **extra,
    }


@pytest.fixture
def held_offices(monkeypatch):
    """Supply governance offices the way hydration does — as a DATABASE answer.

    An office claim placed on the user mapping by the caller is deliberately
    ignored: ``hydrate_authorisation_claims`` overwrites every claim it owns,
    because a claim the caller supplies is not a claim the server verified. That
    is the Phase 2 cross-building leak fix, and a test that put ``ec_position``
    on the subject dict would be asserting the vulnerability still works.

    So the seam to control is hydration itself. This fixture stands in for
    ``governance.ec_members`` returning a term-current office row for the scheme
    the request targets.
    """

    def _set(*offices: str):
        async def fake_hydrate(user, scope, **_hydration_hints):
            return {
                **(user or {}),
                "governance_offices": list(offices),
                "ec_position": offices[0] if offices else None,
                "assigned_building_ids": [BUILDING],
                "managed_building_ids": [BUILDING],
                "active_resolution_ids": [],
                "active_delegation_ids": [],
            }

        monkeypatch.setattr(
            "services.authorisation_context.hydrate_authorisation_claims", fake_hydrate
        )

    return _set


def _app(subject: dict, *, response_model=None, handler=None) -> FastAPI:
    """Build a minimal app whose single route is capability-guarded.

    ``get_current_user`` is overridden rather than mocked at import time so the
    real ``require_capability`` dependency — hydration included — runs.
    """
    from utils.auth import get_current_user

    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    default_payload = {
        "unit_number": "12",
        "owner_name": "A Person",
        "email": "owner@example.com",
        "primary_mobile": "0400000000",
        "net_balance": 1234.56,
        "nested": [{"bsb": "062-000", "account_number": "12345678"}],
    }

    async def route(
        request: Request,
        current_user: dict = Depends(require_capability("building.finance.view", scope_values={"building_id": BUILDING})),
    ):
        if handler is not None:
            return handler(request)
        return default_payload

    app.add_api_route("/probe", route, methods=["GET"], response_model=response_model)
    app.dependency_overrides[get_current_user] = lambda: subject
    return app


# ── Recording ────────────────────────────────────────────────────────────────

class _State:
    pass


class _FakeRequest:
    def __init__(self):
        self.state = _State()


def test_record_obligations_unions_across_decisions():
    """Two capabilities on one route means the stricter combined mask, never the looser."""
    request = _FakeRequest()
    record_obligations(request, ("MASK_BANK_DETAILS",), decision_id="d1")
    record_obligations(request, ("MASK_OWNER_CONTACT", "MASK_BANK_DETAILS"), decision_id="d2")

    assert pending_obligations(request) == ("MASK_BANK_DETAILS", "MASK_OWNER_CONTACT")
    assert decision_ids(request) == ("d1", "d2")


def test_record_obligations_tolerates_no_request():
    """A decision made outside a request context must not raise."""
    record_obligations(None, ("MASK_BANK_DETAILS",))
    assert pending_obligations(None) == ()
    assert decision_ids(None) == ()


def test_apply_pending_obligations_marks_the_request_handled():
    """An in-route mask must stop the middleware walking the payload again."""
    request = _FakeRequest()
    record_obligations(request, ("MASK_OWNER_CONTACT",))

    masked = apply_pending_obligations(request, {"email": "a@b.com", "unit_number": "3"})

    assert masked == {"email": WITHHELD, "unit_number": "3"}
    from services.obligation_enforcement import obligations_applied

    assert obligations_applied(request) is True


def test_apply_pending_obligations_can_add_a_resource_field_mask():
    """A per-resource field_mask narrows further; it can only ADD withheld fields."""
    request = _FakeRequest()
    record_obligations(request, ("MASK_OWNER_CONTACT",))

    masked = apply_pending_obligations(
        request, {"email": "a@b.com", "internal_note": "x"}, extra_fields=("internal_note",)
    )

    assert masked == {"email": WITHHELD, "internal_note": WITHHELD}


# ── End to end through the middleware ────────────────────────────────────────

def test_ec_member_response_is_masked_on_the_wire():
    """The regression this module exists for: an EC member must not receive per-lot arrears.

    ``building.arrears.view`` allows an EC member. The settled decision (plan §8.2)
    is that ordinary EC members get aggregates only — per-lot detail is the
    treasurer's. That restriction is expressed as an obligation, so it is only
    real if it survives serialisation.
    """
    client = TestClient(_app(_user(UserRole.EC_MEMBER)))
    body = client.get("/probe").json()

    assert body["net_balance"] == WITHHELD, "per-lot arrears leaked to an ordinary EC member"
    assert body["email"] == WITHHELD, "owner contact leaked to an ordinary EC member"
    assert body["nested"][0]["bsb"] == WITHHELD, "bank details leaked from a nested object"
    assert body["nested"][0]["account_number"] == WITHHELD
    # Non-sensitive fields are untouched — masking must not blank the response.
    assert body["unit_number"] == "12"
    assert body["owner_name"] == "A Person"


def test_treasurer_sees_arrears_but_still_not_bank_details(held_offices):
    """An office adds function, never rank (plan §4 rule 2).

    The treasurer holds the per-lot arrears function under s 43, so the arrears
    mask lifts. Bank details are withheld from everyone outside the dual-control
    payment flow, including the treasurer.
    """
    held_offices("treasurer")
    body = TestClient(_app(_user(UserRole.EC_MEMBER))).get("/probe").json()

    assert body["net_balance"] == 1234.56, "treasurer must see per-lot arrears (s 43)"
    assert body["nested"][0]["bsb"] == WITHHELD, "bank details are never role-unmasked"


def test_secretary_sees_owner_contact_but_not_arrears(held_offices):
    """The records custodian (s 42) gets contact details; that office is not the treasurer's."""
    held_offices("secretary")
    body = TestClient(_app(_user(UserRole.EC_MEMBER))).get("/probe").json()

    assert body["email"] == "owner@example.com", "secretary is the records custodian (s 42)"
    assert body["net_balance"] == WITHHELD, "per-lot arrears is a treasurer function, not secretary"


def test_a_caller_supplied_office_claim_does_not_unmask(held_offices):
    """The Phase 2 rule, re-pinned at the masking layer.

    Putting ``ec_position="TREASURER"`` on the request's user mapping is not
    holding the treasurer office. Hydration overwrites every claim it owns, so a
    forged claim must not lift the arrears mask. Here hydration reports the
    subject holds NO office, while the caller asserts treasurer.
    """
    held_offices()  # the database says: no office at this scheme
    subject = _user(UserRole.EC_MEMBER, ec_position="TREASURER", governance_offices=["treasurer"])

    body = TestClient(_app(subject)).get("/probe").json()

    assert body["net_balance"] == WITHHELD, "a caller-supplied office claim unmasked arrears"


def test_strata_manager_sees_operational_data_but_not_bank_details():
    """Management needs contact and arrears operationally; bank details stay withheld."""
    body = TestClient(_app(_user(UserRole.STRATA_MANAGER))).get("/probe").json()

    assert body["email"] == "owner@example.com"
    assert body["net_balance"] == 1234.56
    assert body["nested"][0]["bsb"] == WITHHELD


def test_masking_survives_a_response_model():
    """The mechanical reason masking is a middleware rather than a route call.

    FastAPI validates the handler's return value against ``response_model``
    BEFORE serialising. WITHHELD is a string, so masking ``net_balance: float``
    inside the handler would raise ResponseValidationError. Masking after
    serialisation sidesteps that entirely — and this test fails loudly if anyone
    moves the mask back into the route.
    """

    class LedgerRow(BaseModel):
        unit_number: str
        net_balance: float
        email: str

    def handler(_request):
        return {"unit_number": "12", "net_balance": 1234.56, "email": "owner@example.com"}

    app = _app(_user(UserRole.EC_MEMBER), response_model=LedgerRow, handler=handler)
    response = TestClient(app).get("/probe")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["net_balance"] == WITHHELD
    assert body["email"] == WITHHELD


def test_content_length_is_corrected_after_masking():
    """A stale content-length truncates the response at the client."""
    response = TestClient(_app(_user(UserRole.EC_MEMBER))).get("/probe")

    declared = response.headers.get("content-length")
    if declared is not None:
        assert int(declared) == len(response.content)
    # Whole body parsed without error, i.e. it was not truncated.
    json.loads(response.content)


def test_list_responses_are_masked_element_by_element():
    """A list endpoint is where cross-owner disclosure actually happens."""

    def handler(_request):
        return [
            {"unit_number": "1", "net_balance": 10.0},
            {"unit_number": "2", "net_balance": 20.0},
        ]

    body = TestClient(_app(_user(UserRole.EC_MEMBER), handler=handler)).get("/probe").json()

    assert [row["net_balance"] for row in body] == [WITHHELD, WITHHELD]
    assert [row["unit_number"] for row in body] == ["1", "2"]


def test_a_route_that_masked_itself_is_not_masked_twice():
    """mark_obligations_applied must actually suppress the middleware."""

    def handler(request):
        payload = {"email": "owner@example.com", "unit_number": "9"}
        mark_obligations_applied(request)
        return payload

    body = TestClient(_app(_user(UserRole.EC_MEMBER), handler=handler)).get("/probe").json()

    # The route claimed responsibility and (in this deliberately wrong example)
    # did not mask. The middleware honours the claim — which is precisely why
    # mark_obligations_applied() must only be called after actually masking.
    assert body["email"] == "owner@example.com"


def test_denied_requests_are_not_masked_into_a_confusing_error():
    """An owner has no building.finance.view capability; the 403 body stays intact."""
    response = TestClient(_app(_user(UserRole.OWNER))).get("/probe")

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "building.finance.view" in detail
    # The denial carries a correlation id and no reason codes (non-disclosure).
    assert "DENY_" not in detail


def test_unguarded_routes_are_untouched():
    """The middleware must be inert where no authorisation decision was made."""
    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    @app.get("/open")
    async def open_route():
        return {"email": "public@example.com", "bsb": "062-000"}

    body = TestClient(app).get("/open").json()

    assert body == {"email": "public@example.com", "bsb": "062-000"}


# ── Fail-closed: if the mask cannot be applied, nothing is returned ──────────
#
# These four tests originally asserted the opposite — that an unmaskable response
# passed through unmasked with a logged warning. That was a fail-open bypass
# raised in review of PR #672, and a reachable one: a caller who has legitimately
# passed the capability check but is subject to a field mask could steer a guarded
# route into one of these states and receive the very fields being withheld.


def test_non_json_responses_are_refused_not_passed_through():
    """A CSV export from a guarded route is exactly where bulk PII leaves the building.

    The middleware cannot mask CSV, so it must refuse rather than emit owner
    email addresses in the clear. A route that legitimately exports must mask at
    the source and call mark_obligations_applied().
    """
    from starlette.responses import PlainTextResponse

    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    @app.get("/csv")
    async def csv_route(request: Request):
        record_obligations(request, ("MASK_OWNER_CONTACT",))
        return PlainTextResponse("unit,email\n1,owner@example.com\n", media_type="text/csv")

    response = TestClient(app).get("/csv")

    assert response.status_code == 500
    assert "owner@example.com" not in response.text


def test_a_source_masked_non_json_export_is_allowed_through():
    """The escape hatch must actually work, or exports become impossible."""
    from starlette.responses import PlainTextResponse

    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    @app.get("/csv")
    async def csv_route(request: Request):
        record_obligations(request, ("MASK_OWNER_CONTACT",))
        mark_obligations_applied(request)  # route masked it itself
        return PlainTextResponse("unit,email\n1,__withheld__\n", media_type="text/csv")

    response = TestClient(app).get("/csv")

    assert response.status_code == 200
    assert response.text == "unit,email\n1,__withheld__\n"


def test_oversized_bodies_are_refused(caplog):
    """The most exploitable branch: ask for a huge page, receive unmasked arrears."""
    import services.obligation_enforcement as module

    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    @app.get("/big")
    async def big(request: Request):
        record_obligations(request, ("MASK_OWNER_CONTACT",))
        return {"email": "owner@example.com", "padding": "x" * 4096}

    original = module.MAX_MASKABLE_BODY_BYTES
    module.MAX_MASKABLE_BODY_BYTES = 128
    try:
        with caplog.at_level("ERROR"):
            response = TestClient(app).get("/big")
    finally:
        module.MAX_MASKABLE_BODY_BYTES = original

    assert response.status_code == 413
    assert "owner@example.com" not in response.text
    assert any("exceeds" in record.message for record in caplog.records), (
        "a refused response must say why, so the route can be paginated"
    )


def test_unparseable_json_is_refused():
    """A body that claims JSON and is not must not be handed back unmasked."""
    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    @app.get("/broken")
    async def broken(request: Request):
        record_obligations(request, ("MASK_OWNER_CONTACT",))
        return Response(
            content=b"{not json at all: owner@example.com",
            media_type="application/json",
        )

    response = TestClient(app).get("/broken")

    assert response.status_code == 500
    assert "owner@example.com" not in response.text


def test_a_refusal_discloses_nothing_about_what_was_withheld():
    """Same non-disclosure rule assert_capability() follows."""
    import services.obligation_enforcement as module

    app = FastAPI()
    app.add_middleware(ObligationEnforcementMiddleware)

    @app.get("/big")
    async def big(request: Request):
        record_obligations(request, ("MASK_OTHER_OWNER_ARREARS",))
        return {"net_balance": 1234.56, "padding": "x" * 4096}

    original = module.MAX_MASKABLE_BODY_BYTES
    module.MAX_MASKABLE_BODY_BYTES = 128
    try:
        response = TestClient(app).get("/big")
    finally:
        module.MAX_MASKABLE_BODY_BYTES = original

    body = response.text
    assert "MASK_OTHER_OWNER_ARREARS" not in body
    assert "net_balance" not in body
    assert "1234.56" not in body


def test_a_refusal_keeps_the_cors_headers():
    """Without them a browser reports an opaque CORS error, not the 413.

    This middleware is registered OUTSIDE CORSMiddleware, so the CORS headers are
    already on the response being replaced by the time a refusal is built. The
    first implementation returned a bare JSONResponse and dropped them, which
    made a withheld response indistinguishable from a network failure in the
    frontend. Found in the post-implementation audit, not by a failing test.
    """
    from fastapi.middleware.cors import CORSMiddleware

    import services.obligation_enforcement as module

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware, allow_origins=["https://example.com"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(ObligationEnforcementMiddleware)  # outermost, as in server.py

    @app.get("/big")
    async def big(request: Request):
        record_obligations(request, ("MASK_OWNER_CONTACT",))
        return {"email": "owner@example.com", "pad": "x" * 4096}

    original = module.MAX_MASKABLE_BODY_BYTES
    module.MAX_MASKABLE_BODY_BYTES = 128
    try:
        response = TestClient(app).get("/big", headers={"Origin": "https://example.com"})
    finally:
        module.MAX_MASKABLE_BODY_BYTES = original

    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == "https://example.com", (
        "a refused response must still carry CORS headers, or the browser reports "
        "a CORS failure instead of the 413 and the caller cannot tell them apart"
    )
    assert "owner@example.com" not in response.text
