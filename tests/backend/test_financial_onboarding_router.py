from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "backend" / "routers" / "financial_onboarding.py"


def _load_router_module():
    module_name = f"test_financial_onboarding_router_{uuid.uuid4().hex}"

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def __init__(self, prefix: str = ""):
            self.prefix = prefix
            self.routes = []

        def _register(self, path: str):
            def decorator(func):
                self.routes.append(types.SimpleNamespace(path=path, endpoint=func))
                return func

            return decorator

        def post(self, path: str, **_kwargs):
            return self._register(path)

        def get(self, path: str, **_kwargs):
            return self._register(path)

        def delete(self, path: str, **_kwargs):
            return self._register(path)

    def Depends(dependency):
        return dependency

    def Query(default=None, **_kwargs):
        return default

    def File(default=None, **_kwargs):
        return default

    def Form(default=None, **_kwargs):
        return default

    class UploadFile:
        """Minimal stand-in — only the type annotation is referenced at import time in this
        stub environment, no instance behaviour is exercised by these pure-logic tests."""

    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    def Field(default=None, **_kwargs):
        return default

    class EvidenceDocumentCreate:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FundBalance:
        def __init__(self, fund_code: str, amount_cents: int):
            self.fund_code = fund_code
            self.amount_cents = amount_cents

    services_mod = types.ModuleType("services.onboarding.financial_onboarding")
    services_mod.EAST_GATE_BUILDING_ID = "13195"
    services_mod.EvidenceDocumentCreate = EvidenceDocumentCreate
    services_mod.FundBalance = FundBalance

    async def _not_stubbed(*_args, **_kwargs):
        raise AssertionError("stub not replaced in test")

    services_mod.create_evidence_document = _not_stubbed
    services_mod.get_cutover_config = _not_stubbed
    services_mod.onboard_building_financials = _not_stubbed

    db_module = types.ModuleType("database")
    db_module.db = types.SimpleNamespace(_db={})

    auth_module = types.ModuleType("utils.auth")
    auth_module.effective_role = lambda user: user.get("role")
    auth_module.get_current_building = lambda: None
    auth_module.get_current_user = lambda: None

    config_repo_module = types.ModuleType("db_postgres.repos.config_repo")

    async def resolve_scheme_context(_building_id: str):
        return None

    config_repo_module.resolve_scheme_context = resolve_scheme_context

    services_package = types.ModuleType("services")
    services_package.__path__ = []
    services_onboarding_package = types.ModuleType("services.onboarding")
    services_onboarding_package.__path__ = []
    services_package.onboarding = services_onboarding_package
    services_onboarding_package.financial_onboarding = services_mod

    db_postgres_package = types.ModuleType("db_postgres")
    db_postgres_package.__path__ = []
    db_postgres_repos_package = types.ModuleType("db_postgres.repos")
    db_postgres_repos_package.__path__ = []
    db_postgres_package.repos = db_postgres_repos_package
    db_postgres_repos_package.config_repo = config_repo_module

    utils_package = types.ModuleType("utils")
    utils_package.__path__ = []
    utils_package.auth = auth_module

    stubs = {
        "fastapi": types.SimpleNamespace(
            APIRouter=APIRouter, Depends=Depends, HTTPException=HTTPException, Query=Query,
            File=File, Form=Form, UploadFile=UploadFile,
        ),
        "pydantic": types.SimpleNamespace(BaseModel=BaseModel, Field=Field),
        "database": db_module,
        "services": services_package,
        "services.onboarding": services_onboarding_package,
        "utils.auth": auth_module,
        "utils": utils_package,
        "services.onboarding.financial_onboarding": services_mod,
        "db_postgres": db_postgres_package,
        "db_postgres.repos": db_postgres_repos_package,
        "db_postgres.repos.config_repo": config_repo_module,
    }

    previous = {name: sys.modules.get(name) for name in stubs}
    try:
        for name, stub in stubs.items():
            sys.modules[name] = stub
        spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for name, prior in previous.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior


@pytest.mark.asyncio
async def test_require_finance_admin_error_lists_allowed_roles():
    module = _load_router_module()

    with pytest.raises(module.HTTPException) as exc_info:
        module._require_finance_admin({"role": "owner"})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Finance super_admin, strata_admin, or strata_manager role required"


@pytest.mark.parametrize("role", ["super_admin", "strata_admin", "strata_manager"])
def test_require_finance_admin_allows_platform_finance_roles(role):
    module = _load_router_module()

    module._require_finance_admin({"role": role})


@pytest.mark.parametrize("role", ["tenant", "guest", "owner"])
def test_require_finance_admin_blocks_non_admin_roles(role):
    module = _load_router_module()

    with pytest.raises(module.HTTPException):
        module._require_finance_admin({"role": role})


def test_resolved_plan_number_strips_up_prefix():
    module = _load_router_module()

    assert module._resolved_plan_number({"scheme_id": "scheme-1", "scheme_number": "UP13195"}) == "13195"
    assert module._resolved_plan_number({"scheme_id": "scheme-1", "scheme_number": "13195"}) == "13195"


@pytest.mark.asyncio
async def test_create_evidence_document_normalizes_scheme_uuid_to_plan_number(monkeypatch):
    module = _load_router_module()
    scheme_id = str(uuid.uuid4())
    captured = {}

    async def fake_resolve(building_id: str):
        assert building_id == scheme_id
        return {"scheme_id": scheme_id, "scheme_number": "UP13195"}

    async def fake_create(payload):
        captured["building_id"] = payload.building_id
        return {"building_id": payload.building_id}

    monkeypatch.setattr(module, "resolve_scheme_context", fake_resolve)
    monkeypatch.setattr(module, "create_evidence_document", fake_create)

    request = module.EvidenceDocumentRequest(
        document_type="bank_statement",
        file_url="https://example.test/evidence.pdf",
        sha256_hash="a" * 64,
        declared_total_cents=125000,
        declared_totals_by_fund={"admin": 50000, "sinking": 75000},
        metadata={},
        is_test_data=False,
    )
    response = await module.create_financial_evidence_document(
        request=request,
        current_user={"role": "strata_manager", "id": str(uuid.uuid4())},
        building_id=scheme_id,
    )

    assert captured["building_id"] == "13195"
    assert response["building_id"] == "13195"


@pytest.mark.asyncio
async def test_create_evidence_document_allows_non_east_gate_buildings(monkeypatch):
    module = _load_router_module()
    scheme_id = str(uuid.uuid4())

    async def fake_resolve(building_id: str):
        assert building_id == "16244"
        return {"scheme_id": scheme_id, "scheme_number": "16244"}

    async def fake_create(payload):
        return {"building_id": payload.building_id}

    monkeypatch.setattr(module, "resolve_scheme_context", fake_resolve)
    monkeypatch.setattr(module, "create_evidence_document", fake_create)

    request = module.EvidenceDocumentRequest(
        document_type="bank_statement",
        file_url="https://example.test/evidence.pdf",
        sha256_hash="a" * 64,
        declared_total_cents=125000,
        declared_totals_by_fund={"admin": 50000, "sinking": 75000},
        metadata={},
        is_test_data=False,
    )
    response = await module.create_financial_evidence_document(
        request=request,
        current_user={"role": "strata_admin", "id": str(uuid.uuid4())},
        building_id="16244",
    )

    assert response["building_id"] == "16244"


@pytest.mark.asyncio
async def test_assert_building_access_compares_scheme_ids(monkeypatch):
    module = _load_router_module()
    shared_scheme_id = str(uuid.uuid4())
    current_uuid = str(uuid.uuid4())

    async def fake_resolve(building_id: str):
        if building_id in {"13195", current_uuid}:
            return {"scheme_id": shared_scheme_id, "scheme_number": "13195"}
        return None

    monkeypatch.setattr(module, "resolve_scheme_context", fake_resolve)

    resolved_building_id = await module._assert_building_access(
        path_building_id="13195",
        current_building=current_uuid,
        current_user={"role": "strata_manager"},
    )

    assert resolved_building_id == "13195"


@pytest.mark.asyncio
async def test_assert_building_access_blocks_cross_building_requests(monkeypatch):
    module = _load_router_module()
    current_uuid = str(uuid.uuid4())

    async def fake_resolve(building_id: str):
        if building_id == "13195":
            return {"scheme_id": "scheme-a", "scheme_number": "13195"}
        if building_id == current_uuid:
            return {"scheme_id": "scheme-b", "scheme_number": "16244"}
        return None

    monkeypatch.setattr(module, "resolve_scheme_context", fake_resolve)

    with pytest.raises(module.HTTPException) as exc_info:
        await module._assert_building_access(
            path_building_id="13195",
            current_building=current_uuid,
            current_user={"role": "strata_manager"},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_archive_uses_resolved_plan_number_for_mongo_filter(monkeypatch):
    module = _load_router_module()
    path_uuid = str(uuid.uuid4())
    current_uuid = str(uuid.uuid4())
    captured = {}

    async def fake_resolve(building_id: str):
        if building_id in {path_uuid, current_uuid}:
            return {"scheme_id": "scheme-1", "scheme_number": "13195"}
        return None

    class FakeCursor:
        def __init__(self, docs):
            self.docs = docs

        def limit(self, _limit: int):
            return self

        async def to_list(self, length: int):
            return self.docs[:length]

    class FakeCollection:
        def find(self, filter_value):
            captured["filter"] = filter_value
            return FakeCursor([{"building_id": "13195", "amount": 1}])

    monkeypatch.setattr(module, "resolve_scheme_context", fake_resolve)
    module.db._db = {name: FakeCollection() for name in module._FINANCE_ARCHIVE_COLLECTIONS}

    response = await module.get_financial_archive(
        building_id=path_uuid,
        limit=10,
        current_user={"role": "strata_manager"},
        current_building=current_uuid,
    )

    assert captured["filter"] == {"building_id": "13195"}
    assert response["building_id"] == "13195"
