from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_SERVICE_PATH = Path(__file__).resolve().parents[2] / "backend" / "services" / "onboarding" / "financial_onboarding.py"


def _load_service_module():
    module_name = f"test_financial_onboarding_service_{uuid.uuid4().hex}"

    @dataclass(frozen=True)
    class SchemeRef:
        tenant_id: uuid.UUID
        scheme_id: uuid.UUID

    @dataclass(frozen=True)
    class PostGenesisJournalCommand:
        scheme_ref: SchemeRef
        fund_id: uuid.UUID
        opening_balance_cents: int
        as_at_date: date
        evidence_doc_id: uuid.UUID | None = None
        evidence_doc_hash: str | None = None
        posted_by_user_id: uuid.UUID | None = None
        approved_by_user_id: uuid.UUID | None = None
        posted_by_user_name: str | None = None
        building_id: str | None = None
        idempotency_key: str | None = None
        is_test_data: bool = False

    async def _not_stubbed(*_args, **_kwargs):
        raise AssertionError("stub not replaced in test")

    entities_module = types.ModuleType("services.financial_core.domain.entities")
    entities_module.SchemeRef = SchemeRef
    entities_module.PostGenesisJournalCommand = PostGenesisJournalCommand

    services_package = types.ModuleType("services")
    services_package.__path__ = []
    financial_core_domain_package = types.ModuleType("services.financial_core.domain")
    financial_core_domain_package.__path__ = []

    genesis_module = types.ModuleType("services.financial_core.genesis")
    genesis_module.enable_canonical_cutover_overrides = _not_stubbed
    genesis_module.ensure_scheme_finance_bootstrap = _not_stubbed
    genesis_module.normalize_fund_type = lambda fund_type: fund_type
    genesis_module.resolve_scheme_fund_ids = _not_stubbed

    cutover_status_module = types.ModuleType("services.cutover_status_service")
    cutover_status_module.record_financial_foundation_readiness = AsyncMock(return_value={"mode": "mongo_primary"})

    financial_core_module = types.ModuleType("services.financial_core")
    financial_core_module.__path__ = []
    financial_core_module.get_financial_core_service = lambda _session: None
    financial_core_module.domain = financial_core_domain_package
    financial_core_module.genesis = genesis_module
    services_package.financial_core = financial_core_module
    financial_core_domain_package.entities = entities_module

    config_repo_module = types.ModuleType("db_postgres.repos.config_repo")
    config_repo_module.resolve_scheme_context = _not_stubbed

    session_module = types.ModuleType("db_postgres.session")
    session_module.async_session_context = _not_stubbed
    session_module.set_tenant = _not_stubbed

    sqlalchemy_module = types.ModuleType("sqlalchemy")
    sqlalchemy_module.text = lambda query: query

    db_postgres_package = types.ModuleType("db_postgres")
    db_postgres_package.__path__ = []
    db_postgres_repos_package = types.ModuleType("db_postgres.repos")
    db_postgres_repos_package.__path__ = []
    db_postgres_package.repos = db_postgres_repos_package
    db_postgres_package.session = session_module
    db_postgres_repos_package.config_repo = config_repo_module

    stubs = {
        "services": services_package,
        "db_postgres.repos.config_repo": config_repo_module,
        "db_postgres": db_postgres_package,
        "db_postgres.repos": db_postgres_repos_package,
        "db_postgres.session": session_module,
        "services.financial_core": financial_core_module,
        "services.financial_core.domain": financial_core_domain_package,
        "services.financial_core.domain.entities": entities_module,
        "services.financial_core.genesis": genesis_module,
        "services.cutover_status_service": cutover_status_module,
        "sqlalchemy": sqlalchemy_module,
    }

    previous = {name: sys.modules.get(name) for name in stubs}
    try:
        for name, stub in stubs.items():
            sys.modules[name] = stub
        spec = importlib.util.spec_from_file_location(module_name, _SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)
        for name, prior in previous.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior


class _FakeResult:
    def __init__(self, *, first_value=None):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _FakeSession:
    def __init__(self, *, existing_row=None):
        self.existing_row = existing_row
        self.executed = []

    async def execute(self, query, params=None):
        self.executed.append((query, params))
        if "SELECT onboarded" in str(query):
            return _FakeResult(first_value=self.existing_row)
        return _FakeResult()


class _FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeFinancialCore:
    def __init__(self):
        self.commands = []

    async def post_genesis_journal(self, cmd):
        self.commands.append(cmd)
        return types.SimpleNamespace(journal_entry_id=uuid.uuid4())


class _EvidenceResult:
    def __init__(self, *, first_value=None, one_value=None):
        self._first_value = first_value
        self._one_value = one_value

    def mappings(self):
        return self

    def first(self):
        return self._first_value

    def one(self):
        return self._one_value


def _patch_success_dependencies(monkeypatch, module, *, declared_total=125000, existing_row=None):
    scheme_ref = module.SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())
    session = _FakeSession(existing_row=existing_row)
    financial_core = _FakeFinancialCore()

    monkeypatch.setattr(module, "_resolve_scheme_ref", AsyncMock(return_value=scheme_ref))
    monkeypatch.setattr(
        module,
        "async_session_context",
        lambda: _FakeSessionContext(session),
    )
    monkeypatch.setattr(module, "set_tenant", AsyncMock())
    monkeypatch.setattr(
        module,
        "_load_evidence",
        AsyncMock(
            return_value={
                "document_id": str(uuid.uuid4()),
                "declared_total_cents": declared_total,
                "sha256_hash": "b" * 64,
                "approved_by": str(uuid.uuid4()),
                "approved_at": date.today().isoformat(),
                "declared_totals_by_fund": {"admin": 50000, "sinking": 75000},
                "is_test_data": False,
            }
        ),
    )
    monkeypatch.setattr(module, "_validate_approval_user", AsyncMock(return_value="Approver"))
    monkeypatch.setattr(module, "ensure_scheme_finance_bootstrap", AsyncMock())
    monkeypatch.setattr(
        module,
        "resolve_scheme_fund_ids",
        AsyncMock(return_value={"admin": uuid.uuid4(), "sinking": uuid.uuid4(), "special": uuid.uuid4()}),
    )
    monkeypatch.setattr(module, "get_financial_core_service", lambda _session: financial_core)
    monkeypatch.setattr(module, "record_financial_foundation_readiness", AsyncMock(return_value={"mode": "mongo_primary"}))
    return session, financial_core


@pytest.mark.asyncio
async def test_onboard_building_financials_success_path(monkeypatch):
    module = _load_service_module()
    session, financial_core = _patch_success_dependencies(monkeypatch, module)

    result = await module.onboard_building_financials(
        building_id="13195",
        cutover_date=date.today(),
        opening_balances=[
            module.FundBalance(fund_code="admin", amount_cents=50000),
            module.FundBalance(fund_code="sinking", amount_cents=75000),
        ],
        evidence_document_id=str(uuid.uuid4()),
        approved_by_user_id=str(uuid.uuid4()),
    )

    assert result.building_id == "13195"
    assert result.onboarded is True
    assert result.source_of_truth == "mongo"
    assert result.readiness_status == "ready_for_shadow"
    assert len(result.journal_entry_ids) == 2
    assert [cmd.building_id for cmd in financial_core.commands] == ["13195", "13195"]
    assert any("INSERT INTO finance.financial_cutover_config" in str(query) for query, _ in session.executed)
    assert any("INSERT INTO finance.financial_onboarding_audit" in str(query) for query, _ in session.executed)


@pytest.mark.asyncio
async def test_onboard_building_financials_rejects_duplicate_funds(monkeypatch):
    module = _load_service_module()
    monkeypatch.setattr(module, "_resolve_scheme_ref", AsyncMock(return_value=module.SchemeRef(uuid.uuid4(), uuid.uuid4())))

    with pytest.raises(ValueError, match="Duplicate opening balance for fund admin"):
        await module.onboard_building_financials(
            building_id="13195",
            cutover_date=date.today(),
            opening_balances=[
                module.FundBalance(fund_code="admin", amount_cents=50000),
                module.FundBalance(fund_code="administrative", amount_cents=75000),
                module.FundBalance(fund_code="sinking", amount_cents=1000),
            ],
            evidence_document_id=str(uuid.uuid4()),
            approved_by_user_id=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_onboard_building_financials_rejects_declared_total_mismatch(monkeypatch):
    module = _load_service_module()
    _patch_success_dependencies(monkeypatch, module, declared_total=1)

    with pytest.raises(ValueError, match="declared_total_cents"):
        await module.onboard_building_financials(
            building_id="13195",
            cutover_date=date.today(),
            opening_balances=[
                module.FundBalance(fund_code="admin", amount_cents=50000),
                module.FundBalance(fund_code="sinking", amount_cents=75000),
            ],
            evidence_document_id=str(uuid.uuid4()),
            approved_by_user_id=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_onboard_building_financials_blocks_already_onboarded_building(monkeypatch):
    module = _load_service_module()
    existing_row = types.SimpleNamespace(onboarded=True)
    _patch_success_dependencies(monkeypatch, module, existing_row=existing_row)

    with pytest.raises(ValueError, match="already completed"):
        await module.onboard_building_financials(
            building_id="13195",
            cutover_date=date.today(),
            opening_balances=[
                module.FundBalance(fund_code="admin", amount_cents=50000),
                module.FundBalance(fund_code="sinking", amount_cents=75000),
            ],
            evidence_document_id=str(uuid.uuid4()),
            approved_by_user_id=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_onboard_building_financials_enforces_approval_roles(monkeypatch):
    module = _load_service_module()
    _patch_success_dependencies(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "_validate_approval_user",
        AsyncMock(side_effect=PermissionError("Approval user must be super_admin, chairman, or strata_manager")),
    )

    with pytest.raises(PermissionError, match="Approval user must be super_admin, chairman, or strata_manager"):
        await module.onboard_building_financials(
            building_id="13195",
            cutover_date=date.today(),
            opening_balances=[
                module.FundBalance(fund_code="admin", amount_cents=50000),
                module.FundBalance(fund_code="sinking", amount_cents=75000),
            ],
            evidence_document_id=str(uuid.uuid4()),
            approved_by_user_id=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_onboard_building_financials_rejects_unapproved_evidence(monkeypatch):
    module = _load_service_module()
    _patch_success_dependencies(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "_load_evidence",
        AsyncMock(
            return_value={
                "document_id": str(uuid.uuid4()),
                "declared_total_cents": 125000,
                "sha256_hash": "b" * 64,
                "approved_by": None,
                "approved_at": None,
                "declared_totals_by_fund": {"admin": 50000, "sinking": 75000},
                "is_test_data": False,
            }
        ),
    )

    with pytest.raises(ValueError, match="Approved evidence is required"):
        await module.onboard_building_financials(
            building_id="13195",
            cutover_date=date.today(),
            opening_balances=[
                module.FundBalance(fund_code="admin", amount_cents=50000),
                module.FundBalance(fund_code="sinking", amount_cents=75000),
            ],
            evidence_document_id=str(uuid.uuid4()),
            approved_by_user_id=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_create_evidence_document_inserts_approved_evidence(monkeypatch):
    module = _load_service_module()
    scheme_ref = module.SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())
    uploaded_by = uuid.uuid4()
    approved_by = uuid.uuid4()
    session = _FakeSession()

    async def fake_execute(query, params=None):
        sql = str(query)
        session.executed.append((query, params))
        if "SELECT document_id::text, uploaded_at, approved_by::text, approved_at" in sql:
            return _EvidenceResult(first_value=None)
        return _EvidenceResult(one_value=types.SimpleNamespace(document_id=uuid.uuid4(), uploaded_at=date.today()))

    session.execute = fake_execute

    monkeypatch.setattr(module, "_resolve_scheme_ref", AsyncMock(return_value=scheme_ref))
    monkeypatch.setattr(module, "async_session_context", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(module, "set_tenant", AsyncMock())
    monkeypatch.setattr(module, "_validate_active_user", AsyncMock(return_value={"user_id": str(uploaded_by)}))
    monkeypatch.setattr(module, "_validate_approval_user", AsyncMock(return_value="Approver"))

    result = await module.create_evidence_document(
        module.EvidenceDocumentCreate(
            building_id="13195",
            document_type="bank_statement",
            file_url="s3://bucket/opening-balance.pdf",
            sha256_hash="a" * 64,
            uploaded_by=str(uploaded_by),
            approved_by=str(approved_by),
            declared_total_cents=125000,
            declared_totals_by_fund={"admin": 50000, "sinking": 75000},
            metadata={"source": "fixture"},
        )
    )

    assert result["approved_by"] == str(approved_by)
    assert result["declared_totals_by_fund"] == {"admin": 50000, "sinking": 75000}
    assert any("INSERT INTO finance.evidence_documents" in str(query) for query, _ in session.executed)


@pytest.mark.asyncio
async def test_create_evidence_document_is_idempotent_for_duplicate_sha(monkeypatch):
    module = _load_service_module()
    scheme_ref = module.SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())
    session = _FakeSession()
    existing_id = uuid.uuid4()
    uploaded_by = uuid.uuid4()
    approved_by = uuid.uuid4()
    stored_approved_by = uuid.uuid4()

    async def fake_execute(query, params=None):
        sql = str(query)
        session.executed.append((query, params))
        if "SELECT document_id::text, uploaded_at, approved_by::text, approved_at" in sql:
            return _EvidenceResult(
                first_value={
                    "document_id": str(existing_id),
                    "uploaded_at": date.today(),
                    "approved_by": str(stored_approved_by),
                    "approved_at": date.today(),
                }
            )
        return _EvidenceResult(one_value=types.SimpleNamespace(document_id=existing_id, uploaded_at=date.today()))

    session.execute = fake_execute

    monkeypatch.setattr(module, "_resolve_scheme_ref", AsyncMock(return_value=scheme_ref))
    monkeypatch.setattr(module, "async_session_context", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(module, "set_tenant", AsyncMock())
    monkeypatch.setattr(module, "_validate_active_user", AsyncMock(return_value={"user_id": str(uploaded_by)}))
    monkeypatch.setattr(module, "_validate_approval_user", AsyncMock(return_value="Approver"))

    result = await module.create_evidence_document(
        module.EvidenceDocumentCreate(
            building_id="13195",
            document_type="bank_statement",
            file_url="s3://bucket/opening-balance.pdf",
            sha256_hash="b" * 64,
            uploaded_by=str(uploaded_by),
            approved_by=str(approved_by),
            declared_total_cents=125000,
            declared_totals_by_fund={"admin": 50000, "sinking": 75000},
            metadata={},
        )
    )

    assert result["document_id"] == str(existing_id)
    assert result["approved_by"] == str(stored_approved_by)
