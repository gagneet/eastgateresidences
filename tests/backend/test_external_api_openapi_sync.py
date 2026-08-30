"""CI guard: the committed OpenAPI spec must match the External API v1 router.

Wraps scripts/validation/validate_external_api_openapi.py so the drift check runs
in the backend test suite. Pure stdlib (ast + json) — no backend imports, no DB.
"""
import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "validation" / "validate_external_api_openapi.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_external_api_openapi", _VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_api_openapi_spec_in_sync():
    validator = _load_validator()
    router = validator.router_operations()
    spec = validator.spec_operations()

    assert router, "no /api/v1 operations parsed from external_api.py"
    assert spec, "no /api/v1 operations found in the committed OpenAPI spec"

    missing_in_spec = sorted(router - spec)
    stale_in_spec = sorted(spec - router)

    assert not missing_in_spec, (
        "External API v1 routes missing from the OpenAPI spec — regenerate with "
        "scripts/docs/generate_api_artifacts.py: " + str(missing_in_spec)
    )
    assert not stale_in_spec, (
        "OpenAPI spec has /api/v1 routes not in the router — regenerate with "
        "scripts/docs/generate_api_artifacts.py: " + str(stale_in_spec)
    )
