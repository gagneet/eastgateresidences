from fastapi import Request

from utils.error_response import build_error_payload, build_error_response


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/example",
            "headers": headers or [],
        }
    )


def test_build_error_payload_uses_safe_defaults_and_request_id():
    request = _request([(b"x-request-id", b"req-123")])

    payload = build_error_payload(request, status_code=404)

    assert payload == {
        "error": {
            "code": "NOT_FOUND",
            "message": "We could not find that page or API route.",
            "status": 404,
            "request_id": "req-123",
            "retryable": False,
        }
    }


def test_build_error_payload_normalises_structured_http_detail():
    request = _request()

    payload = build_error_payload(
        request,
        status_code=403,
        detail={
            "code": "FEATURE_DISABLED",
            "message": "This feature is not available for your building yet.",
            "feature_key": "maintenance",
            "retryable": False,
        },
    )

    assert payload["error"]["code"] == "FEATURE_DISABLED"
    assert payload["error"]["message"] == "This feature is not available for your building yet."
    assert payload["error"]["metadata"] == {"feature_key": "maintenance"}
    assert payload["error"]["retryable"] is False


def test_build_error_response_json_encodes_non_primitive_detail_values():
    request = _request()

    response = build_error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        detail={"fields": [{"loc": ("body", "due_date"), "msg": "Invalid date"}]},
    )

    assert response.status_code == 422
    assert response.headers["x-request-id"]
    assert b'"loc":["body","due_date"]' in response.body
