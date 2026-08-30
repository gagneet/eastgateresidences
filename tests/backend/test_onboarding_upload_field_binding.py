"""The wizard's multipart field names must actually bind to the endpoints.

This is the defect that made every historical upload in the Onboarding Wizard
return 422: the UI posted one field named `file`, and no endpoint declares that
name. It survived a fully green backend suite because every backend test posts the
correct names — nothing exercised the contract from the caller's side.

So these tests drive the REAL router through FastAPI's real form parsing, rather
than reading signatures. Auth is overridden and the Postgres session is replaced
with a sentinel-raising stub, which makes the assertion binary and hermetic:

  * body rejected  -> 422, handler never entered
  * body accepted  -> handler entered, sentinel escapes, status is NOT 422

No database is touched.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SESSION_ID = "11111111-1111-1111-1111-111111111111"


class _ReachedHandler(RuntimeError):
    """Raised in place of opening a DB session — proves the body bound."""


@pytest.fixture()
def client(monkeypatch):
    from routers import onboarding as onboarding_router
    from utils.auth import get_current_user

    @contextlib.asynccontextmanager
    async def _no_db(*_args, **_kwargs):
        raise _ReachedHandler("handler entered — request body bound successfully")
        yield  # pragma: no cover

    monkeypatch.setattr(onboarding_router, "async_session_context", _no_db)

    app = FastAPI()
    app.include_router(onboarding_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "role": "super_admin",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    }
    return TestClient(app, raise_server_exceptions=False)


def _url(path: str) -> str:
    return f"/api/onboarding/scheme/{SESSION_ID}/{path}"


def _csv(payload: bytes = b"a,b\n1,2\n"):
    return ("f.csv", payload, "text/csv")


# ── The five-file group ───────────────────────────────────────────────────────

FIVE_FILES = {
    "quarterly_levies": _csv(
        b"year,quarter,lot_number,admin_levy_amount,sinking_levy_amount\n2025,1,1,10,5\n"
    ),
    "admin_fund_summary": _csv(b"year,levy_income\n2025,10\n"),
    "sinking_fund_summary": _csv(b"year,levy_income\n2025,5\n"),
    "arrears": _csv(b"lot_number,admin_arrears,sinking_arrears\n1,0,0\n"),
    "outstanding": _csv(b"lot_number,admin_outstanding,sinking_outstanding\n1,0,0\n"),
}


def test_generic_file_field_is_rejected_and_names_every_missing_field(client):
    """Reproduces the original defect exactly."""
    res = client.post(_url("import-historical-financials"), files={"file": _csv()})
    assert res.status_code == 422
    missing = {tuple(e["loc"])[-1] for e in res.json()["detail"]}
    assert missing == set(FIVE_FILES), (
        "the endpoint must report all five required files as missing"
    )


def test_all_five_declared_fields_bind(client):
    res = client.post(_url("import-historical-financials"), files=FIVE_FILES)
    assert res.status_code != 422, (
        f"the five declared field names did not bind: {res.text[:300]}"
    )


def test_a_partial_five_file_group_is_still_rejected(client):
    """The endpoint cross-validates the files, so a partial upload must not proceed."""
    partial = {k: v for k, v in FIVE_FILES.items() if k != "outstanding"}
    res = client.post(_url("import-historical-financials"), files=partial)
    assert res.status_code == 422
    assert {tuple(e["loc"])[-1] for e in res.json()["detail"]} == {"outstanding"}


# ── The single-file endpoints ─────────────────────────────────────────────────

# The payload is the REAL downloadable template for each endpoint, so this doubles
# as a download-then-upload round-trip check. Using a dummy CSV here would fail on
# the `required_columns` gate — also a 422 — and mask whether the field bound at all.
@pytest.mark.parametrize(
    ("path", "field", "template_type"),
    [
        ("import-owner-transfers", "transfers_file", "owner_transfers"),
        ("import-opening-balances", "opening_balances_file", "opening_balances"),
        ("import-historical-expenses", "expenses", "historical_expenses"),
    ],
)
def test_single_file_endpoints_declare_a_specific_field_name(client, path, field, template_type):
    from services.onboarding_templates import render_template_csv, render_template_xlsx

    wrong = client.post(_url(path), files={"file": _csv()})
    assert wrong.status_code == 422, f"{path} unexpectedly accepted a field named 'file'"
    assert {tuple(e["loc"])[-1] for e in wrong.json()["detail"]} == {field}

    for label, payload, mime in (
        ("csv", render_template_csv(template_type), "text/csv"),
        (
            "xlsx",
            render_template_xlsx(template_type),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ):
        res = client.post(_url(path), files={field: (f"t.{label}", payload, mime)})
        assert res.status_code != 422, (
            f"{path} rejected its own {label.upper()} template on field {field!r}: {res.text[:300]}"
        )


def test_wizard_field_names_match_what_the_endpoints_bind():
    """Ties the JSX contract to the binding proved above, in one assertion."""
    import re

    jsx = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/pages/dashboard/admin/OnboardingWizard.jsx"
    )
    if not jsx.exists():  # pragma: no cover - frontend absent
        pytest.skip("frontend not present")
    src = jsx.read_text()
    block = src[src.index("const UPLOAD_STEP_SPECS = {"): src.index("const UPLOAD_STEPS_WITHOUT_ENDPOINT")]
    posted = set(re.findall(r"field:\s*'([a-z_]+)'", block))
    bindable = set(FIVE_FILES) | {"transfers_file", "opening_balances_file", "expenses"}
    assert posted <= bindable, f"wizard posts fields no endpoint binds: {posted - bindable}"
    assert "file" not in posted
