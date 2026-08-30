"""Input-length bounds on request models — security audit 2026-08-26, finding 3.

The finding was "multiple Pydantic request models accept unbounded string or list
fields". These tests pin the three things that have to stay true for the fix to keep
meaning anything:

  1. A request model rejects an over-long string / over-long list (the DoS bound).
  2. A *response* model that subclasses a bounded request model does NOT inherit the
     bound, and keeps the parent's defaults and validation aliases. FastAPI validates
     outbound payloads too, so a leaked bound turns one over-long stored document into
     a 500 for a whole list endpoint — the same failure mode LevyCategoryResponse
     already carries a comment about.
  3. The digital-twin PUT path is bounded. It used to take a bare `dict` and `$set` it,
     which bypassed every model bound; the bound is only real if the update model is
     wired in.

Two claims from the audit report are deliberately NOT implemented, and are pinned here
so a later pass does not "restore" them:
  - models/ballot_audit.py holds no request body (see test_ballot_audit_*).
  - Bounds are set well above observed live data, not at the report's suggested 200 chars.
"""
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from models import digital_twin as dt  # noqa: E402
from models import external_api_models as ext  # noqa: E402
from models import finance as fin  # noqa: E402
from models import payment_approvals as pa  # noqa: E402


def _max_length(model: type[BaseModel], field: str):
    """The max_length constraint on a field, or None if unbounded."""
    info = model.model_fields[field]
    for meta in info.metadata:
        if getattr(meta, "max_length", None) is not None:
            return meta.max_length
    return None


# Every (model, field) that must reject an over-long string, across all four files
# the audit named that actually carry request bodies.
BOUNDED_STRING_FIELDS = [
    (fin.LevyCategoryCreate, "name"),
    (fin.LevyCategoryCreate, "description"),
    (fin.LevyCategoryCreate, "year"),
    (fin.LevyCategoryUpdate, "description"),
    (fin.LevyPaymentCreate, "unit_number"),
    (fin.LevyPaymentCreate, "payment_reference"),
    (fin.LevyPaymentCreate, "notes"),
    (fin.LevyPaymentVerify, "notes"),
    (fin.LevyPaymentReject, "rejection_reason"),
    (fin.FinancialProjectionCreate, "projection_name"),
    (fin.BudgetProposalCreate, "target_year"),
    (fin.BudgetProposalItem, "name"),
    (fin.ExpenseTransactionCreate, "supplier_name"),
    (fin.ExpenseTransactionCreate, "description"),
    (fin.ExpenseTransactionCreate, "invoice_number"),
    (fin.IncomeTransactionCreate, "source"),
    (fin.SpecialLevyCreate, "title"),
    (fin.SpecialLevyCreate, "description"),
    (fin.BankReconciliationCreate, "bank_statement_reference"),
    (fin.InsurancePolicyCreate, "policy_number"),
    (fin.InsurancePolicyCreate, "insurer"),
    (fin.SpecialResolutionRateCreate, "notes"),
    (fin.ContactLogEntry, "description"),
    (fin.AnnualLevyCreate, "year"),
    (pa.SpecialPaymentCreate, "payee_bsb"),
    (pa.SpecialPaymentCreate, "category"),
    (pa.SpecialPaymentCreate, "invoice_reference"),
    (pa.SpecialPaymentCreate, "invoice_url"),
    (pa.SpecialPaymentCreate, "payment_date"),
    (pa.SpecialPaymentCreate, "fund_type"),
    (pa.SpecialPaymentCreate, "notes"),
    (pa.SpecialPaymentApproval, "reason"),
    (ext.APIKeyCreate, "expires_at"),
    (ext.WebhookCreate, "url"),
    (ext.MaintenanceStatusUpdate, "status"),
    (ext.QuoteSubmit, "work_order_id"),
    (ext.QuoteSubmit, "valid_until"),
    (ext.InvoiceSubmit, "work_order_id"),
    (ext.InvoiceSubmit, "invoice_date"),
    (dt.Zone, "name"),
    (dt.Zone, "description"),
    (dt.Facility, "name"),
    (dt.Facility, "category"),
    (dt.BuildingAsset, "name"),
    (dt.BuildingAsset, "notes"),
    (dt.BenefitGroup, "name"),
    (dt.AllocationRule, "allocation_type"),
    (dt.ZoneUpdate, "name"),
    (dt.FacilityUpdate, "notes"),
    (dt.BuildingAssetUpdate, "name"),
    (dt.BenefitGroupUpdate, "description"),
]


@pytest.mark.parametrize(
    "model,field",
    BOUNDED_STRING_FIELDS,
    ids=[f"{m.__name__}.{f}" for m, f in BOUNDED_STRING_FIELDS],
)
def test_string_field_is_bounded(model, field):
    """Every listed request field carries a max_length, and it actually rejects."""
    limit = _max_length(model, field)
    assert limit is not None, f"{model.__name__}.{field} is unbounded"

    with pytest.raises(ValidationError) as exc:
        model.model_validate({field: "x" * (limit + 1)})
    assert any(
        e["loc"] == (field,) and e["type"] == "string_too_long" for e in exc.value.errors()
    ), f"{model.__name__}.{field} did not reject a {limit + 1}-char value"


BOUNDED_LIST_FIELDS = [
    (fin.AnnualLevyCreate, "payment_schedule"),
    (fin.BudgetProposalCreate, "items"),
    (fin.ProjectionAssumptions, "major_works"),
    (fin.PaymentPlanCreate, "instalments"),
    (fin.ArrearsMetadata, "contact_log"),
    (pa.SpecialPaymentCreate, "supporting_documents"),
    (ext.APIKeyCreate, "scopes"),
    (ext.WebhookCreate, "events"),
    (dt.BenefitGroup, "lot_numbers"),
    (dt.BenefitGroupUpdate, "lot_numbers"),
]


@pytest.mark.parametrize(
    "model,field",
    BOUNDED_LIST_FIELDS,
    ids=[f"{m.__name__}.{f}" for m, f in BOUNDED_LIST_FIELDS],
)
def test_list_field_item_count_is_bounded(model, field):
    """List inputs cap the item COUNT.

    Pydantic v2 removed `max_items`; `max_length` on a list field is the item-count
    bound. A bound expressed as `max_items=` would be silently ignored, so this asserts
    the rejection, not just the presence of a constraint.
    """
    limit = _max_length(model, field)
    assert limit is not None, f"{model.__name__}.{field} has no item-count bound"

    with pytest.raises(ValidationError) as exc:
        model.model_validate({field: [{} if field != "lot_numbers" and field != "scopes"
                                      and field != "events" and field != "supporting_documents"
                                      and field != "major_works" else "x"] * (limit + 1)})
    assert any(e["type"] == "too_long" for e in exc.value.errors()), (
        f"{model.__name__}.{field} accepted {limit + 1} items"
    )


def test_scope_and_event_list_items_are_individually_bounded():
    """A count bound alone still allows one multi-megabyte item."""
    with pytest.raises(ValidationError):
        ext.APIKeyCreate(name="k", scopes=["x" * 5_000])
    with pytest.raises(ValidationError):
        ext.WebhookCreate(url="https://example.test/hook", events=["x" * 5_000])
    with pytest.raises(ValidationError):
        dt.BenefitGroup(
            id="1", building_id="13195", name="g", lot_numbers=["x" * 5_000],
            allocation_rule=dt.AllocationRule(allocation_type="equal_split"),
        )


# ── Response models must not inherit the input bounds ────────────────────────────

RESPONSE_PAIRS = [
    (fin.ExpenseTransactionCreate, fin.ExpenseTransactionResponse),
    (fin.IncomeTransactionCreate, fin.IncomeTransactionResponse),
    (fin.SpecialLevyCreate, fin.SpecialLevyResponse),
    (fin.BankReconciliationCreate, fin.BankReconciliationResponse),
    (fin.InsurancePolicyCreate, fin.InsurancePolicyResponse),
    (fin.PaymentPlanCreate, fin.PaymentPlanResponse),
]


@pytest.mark.parametrize(
    "create,response",
    RESPONSE_PAIRS,
    ids=[r.__name__ for _, r in RESPONSE_PAIRS],
)
def test_response_model_does_not_inherit_input_bounds(create, response):
    """GET handlers validate rows importers/scrapers wrote without the Create model."""
    for name in create.model_fields:
        assert _max_length(response, name) is None, (
            f"{response.__name__}.{name} inherited an input bound — one over-long "
            f"stored row would now 500 the whole endpoint"
        )


@pytest.mark.parametrize(
    "create,response",
    RESPONSE_PAIRS,
    ids=[r.__name__ for _, r in RESPONSE_PAIRS],
)
def test_response_model_keeps_parent_defaults_and_aliases(create, response):
    """Relaxing a field must not silently change requiredness, default or alias.

    Re-declaring a field in a subclass drops the parent's `Field(...)` wholesale, so a
    careless relaxation would make `building_id` required or lose the
    AliasChoices("building_id", "plan_id") that lets legacy plan_id-only documents load.
    """
    for name, parent in create.model_fields.items():
        child = response.model_fields[name]
        assert child.is_required() == parent.is_required(), f"{response.__name__}.{name} requiredness"
        assert child.default == parent.default, f"{response.__name__}.{name} default"
        assert (child.validation_alias is None) == (parent.validation_alias is None), (
            f"{response.__name__}.{name} validation alias"
        )


def test_expense_response_accepts_an_over_long_legacy_row():
    """End-to-end version of the two tests above, on the endpoint that reads scraper rows."""
    row = fin.ExpenseTransactionResponse(
        financial_year="2026",
        supplier_name="x" * 40_000,
        description="y" * 100_000,
        plan_id="13195",  # legacy doc with no building_id key
        id="1", created_at="t", updated_at="t", created_by="u",
    )
    assert row.building_id == "13195", "AliasChoices fallback from plan_id was lost"
    assert len(row.description) == 100_000


# ── Claims from the audit report that are deliberately not implemented ───────────

def test_ballot_audit_models_are_not_request_bodies():
    """The report named models/ballot_audit.py; no endpoint binds anything in it.

    BallotEntry / BallotSeal are built inside routers/voting.py::close_ballot from
    agm_votes rows that are already in the database. Bounding them would add no DoS
    protection and would risk 500ing the ballot close on a legacy row. The user input
    that feeds the chain arrives via server.py::AGMVoteCreate, which is bounded instead.
    """
    router_src = (BACKEND / "routers" / "voting.py").read_text()
    for symbol in ("BallotEntry", "BallotSeal", "BallotEntryCreate", "BallotSealCreate"):
        assert f"data: {symbol}" not in router_src and f"payload: {symbol}" not in router_src

    import server  # noqa: PLC0415  — imported lazily; pulls in the whole app

    assert _max_length(server.AGMVoteCreate, "motion_id") is not None
    assert _max_length(server.AGMVoteCreate, "proxy_for") is not None
    with pytest.raises(ValidationError):
        server.AGMVoteCreate(motion_id="m" * 5_000, vote="for")


def test_free_text_bounds_are_above_real_world_content():
    """Report suggested 200–2000 chars; that is too tight for reconstructed/OCR text.

    Bounds are anchored to observed data (widest string in live Mongo across these
    collections is 42 chars) with a wide margin, not to the report's round numbers.
    """
    assert fin.MAX_TEXT_LEN >= 5_000
    assert _max_length(fin.ExpenseTransactionCreate, "description") >= 5_000
    fin.ExpenseTransactionCreate(
        financial_year="2026", supplier_name="Acme Waterproofing Pty Ltd",
        description="A" * 4_999,
    )


# ── The digital-twin PUT path (the bound is only real if it is wired in) ─────────

def test_digital_twin_put_handlers_take_bounded_update_models():
    """They used to take a bare `dict` and `$set` it, bypassing every bound above."""
    src = (BACKEND / "routers" / "digital_twin.py").read_text()
    assert "data: dict" not in src, "a PUT handler still accepts an unvalidated dict body"
    for model in ("ZoneUpdate", "FacilityUpdate", "BuildingAssetUpdate", "BenefitGroupUpdate"):
        assert f"data: {model}" in src


def test_update_fields_drops_unknown_keys_and_keeps_partial_semantics():
    from fastapi import HTTPException

    from routers import digital_twin as router

    # A field the caller did send is kept, including an explicit null (which is how the
    # settings page clears zone_id/notes) — `exclude_unset` distinguishes that from absent.
    assert router._update_fields(dt.ZoneUpdate(name="Tower A")) == {"name": "Tower A"}
    assert router._update_fields(dt.FacilityUpdate(notes=None)) == {"notes": None}

    # Unset fields are absent, so a partial update never clobbers with a default.
    assert "description" not in router._update_fields(dt.ZoneUpdate(name="Tower A"))

    # An empty $set is a pymongo error, and dropping unknown keys makes it reachable.
    with pytest.raises(HTTPException) as exc:
        router._update_fields(dt.ZoneUpdate())
    assert exc.value.status_code == 400
