import importlib.util
from pathlib import Path


def _load_generator():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "docs" / "generate_api_artifacts.py"
    spec = importlib.util.spec_from_file_location("generate_api_artifacts", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_external_api_partner_requests_use_api_key_auth():
    generator = _load_generator()

    request = generator.make_request(
        "GET /api/v1/building",
        "GET",
        "/api/v1/building",
        {"summary": "Get building information"},
        {"components": {"schemas": {}}},
    )["request"]

    assert request["auth"] == {"type": "noauth"}
    assert {"key": "X-API-Key", "value": "{{api_key}}", "description": request["header"][-1]["description"]} in request["header"]


def test_external_api_key_management_requests_remain_bearer_authenticated():
    generator = _load_generator()

    request = generator.make_request(
        "GET /api/v1/api-keys",
        "GET",
        "/api/v1/api-keys",
        {"summary": "List API keys"},
        {"components": {"schemas": {}}},
    )["request"]

    assert request["auth"]["type"] == "bearer"
    assert all(header.get("key") != "X-API-Key" for header in request["header"])


def test_external_api_health_request_is_public():
    generator = _load_generator()

    request = generator.make_request(
        "GET /api/v1/health",
        "GET",
        "/api/v1/health",
        {"summary": "API health check"},
        {"components": {"schemas": {}}},
    )["request"]

    assert request["auth"] == {"type": "noauth"}
    assert all(header.get("key") != "X-API-Key" for header in request["header"])


def test_public_non_external_request_is_noauth_in_postman():
    generator = _load_generator()

    request = generator.make_request(
        "POST /api/auth/register/staff",
        "POST",
        "/api/auth/register/staff",
        {"summary": "Staff self-registration", "security": []},
        {"components": {"schemas": {}}},
    )["request"]

    assert request["auth"] == {"type": "noauth"}
    assert all(header.get("key") != "X-API-Key" for header in request["header"])


def test_external_api_scopes_request_uses_api_key_auth():
    generator = _load_generator()

    request = generator.make_request(
        "GET /api/v1/scopes",
        "GET",
        "/api/v1/scopes",
        {"summary": "List available API scopes"},
        {"components": {"schemas": {}}},
    )["request"]

    assert request["auth"] == {"type": "noauth"}
    assert any(header.get("key") == "X-API-Key" for header in request["header"])


def test_postman_environment_contains_external_api_key_variable():
    generator = _load_generator()
    collection, environment = generator.generate_postman(
        {"paths": {}, "components": {"schemas": {}}},
        [],
        "2026-07-12",
    )

    collection_variables = {item["key"]: item["value"] for item in collection["variable"]}
    environment_values = {item["key"]: item for item in environment["values"]}

    assert collection_variables["api_key"] == ""
    assert environment_values["api_key"]["type"] == "secret"


def test_openapi_auth_metadata_matches_browser_swagger_requirements():
    generator = _load_generator()
    schema = generator.load_schema()

    assert schema["paths"]["/api/auth/login"]["post"]["security"] == []
    assert schema["paths"]["/api/auth/register/staff"]["post"]["security"] == []
    assert schema["paths"]["/api/v1/health"]["get"]["security"] == []
    assert schema["paths"]["/api/v1/scopes"]["get"]["security"] == [{"APIKeyHeader": []}]
    assert schema["paths"]["/api/v1/building"]["get"]["security"] == [{"APIKeyHeader": []}]
    assert schema["paths"]["/api/v1/api-keys"]["get"]["security"] == [{"HTTPBearer": []}]
    assert schema["paths"]["/api/trust/v2/financial-summary"]["get"]["security"] == [{"HTTPBearer": []}]
