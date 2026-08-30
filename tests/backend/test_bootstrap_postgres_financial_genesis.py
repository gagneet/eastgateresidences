from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "bootstrap_postgres_financial_genesis.py"


def _load_script_module():
    module_name = f"test_bootstrap_postgres_financial_genesis_{uuid.uuid4().hex}"

    class EvidenceDocumentCreate:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FundBalance:
        def __init__(self, fund_code: str, amount_cents: int):
            self.fund_code = fund_code
            self.amount_cents = amount_cents

    async def _not_stubbed(*_args, **_kwargs):
        raise AssertionError("stub not replaced in test")

    asyncpg_module = types.ModuleType("asyncpg")
    asyncpg_module.Connection = object

    services_package = types.ModuleType("services")
    services_package.__path__ = []
    services_onboarding_package = types.ModuleType("services.onboarding")
    services_onboarding_package.__path__ = []
    onboarding_module = types.ModuleType("services.onboarding.financial_onboarding")
    onboarding_module.EvidenceDocumentCreate = EvidenceDocumentCreate
    onboarding_module.FundBalance = FundBalance
    onboarding_module.create_evidence_document = _not_stubbed
    onboarding_module.onboard_building_financials = _not_stubbed
    services_package.onboarding = services_onboarding_package
    services_onboarding_package.financial_onboarding = onboarding_module

    stubs = {
        "asyncpg": asyncpg_module,
        "services": services_package,
        "services.onboarding": services_onboarding_package,
        "services.onboarding.financial_onboarding": onboarding_module,
    }

    previous = {name: sys.modules.get(name) for name in stubs}
    try:
        for name, stub in stubs.items():
            sys.modules[name] = stub
        spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_PATH)
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


class _FakeConnection:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetchrow(self, *_args, **_kwargs):
        if not self._rows:
            return None
        return self._rows.pop(0)


@pytest.mark.asyncio
async def test_resolve_building_identifier_normalizes_up_prefix_from_scheme_id_lookup():
    module = _load_script_module()
    conn = _FakeConnection([{"scheme_number": "UP13195", "building_slug": "east-gate"}])

    resolved = await module._resolve_building_identifier(conn, building_id=None, scheme_id=str(uuid.uuid4()), plan_number=None, building_slug=None)

    assert resolved == "13195"


@pytest.mark.asyncio
async def test_resolve_building_identifier_normalizes_up_prefix_from_building_slug_lookup():
    module = _load_script_module()
    conn = _FakeConnection([{"scheme_number": "UP16244"}])

    resolved = await module._resolve_building_identifier(conn, building_id=None, scheme_id=None, plan_number=None, building_slug="sample-slug")

    assert resolved == "16244"
