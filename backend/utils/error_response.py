# @featuretrace:error-recovery-framework — Structured API error envelopes and request ID helpers.
# Layer: service
# Data flow: FastAPI exception handlers -> build_error_response() -> JSON error envelope (global).
# Related: backend/server.py
#          frontend/src/lib/api-error.ts
#          docs/architecture/error-recovery-framework.md
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

SAFE_STATUS_MESSAGES = {
    400: "We could not process that request. Please check the details and try again.",
    401: "Please sign in to continue.",
    403: "You do not have access to this area.",
    404: "We could not find that page or API route.",
    409: "This request conflicts with the current record state.",
    422: "Some required information is missing or invalid.",
    429: "Too many requests. Please wait a moment and try again.",
    500: "Something went wrong on our side.",
    502: "The service is temporarily unavailable.",
    503: "The service is temporarily unavailable.",
}

STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "SERVER_ERROR",
    502: "SERVICE_UNAVAILABLE",
    503: "SERVICE_UNAVAILABLE",
}


# Matches the credential half of a connection URI, e.g.
#   mongodb://eastgate_admin:s3cr3t@host:27018/  ->  mongodb://eastgate_admin:***@host:27018/
# Both MONGO_URL and DATABASE_URL embed a password, and driver connection errors
# routinely echo the full URI back in their exception text.
#
# EVERY quantifier here is explicitly bounded, and that is load-bearing rather than
# cosmetic. The unbounded form of this pattern is super-linear: `re.sub` restarts a
# match attempt at each offset, and an unbounded scheme quantifier rescans the whole
# tail every time. Measured on this machine, the unbounded version took 14.8s on a
# 100 KB run of a single repeated character and could not finish 1 MB at all, while
# the bounded version below does 5 MB in ~0.4s. That input is reachable: this runs
# over scraper stdout, and the news scraper prints content harvested from external
# pages. Bounds are generous versus real URIs (schemes are <16 chars; a 256-char
# user or password is already implausible) and a longer one simply is not redacted
# by this pass — it is not silently truncated.
_URI_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]{0,15}://)"
    r"(?P<user>[^:/@\s]{1,256}):"
    r"(?P<secret>[^@/\s]{1,256})@"
)

# Environment variables whose *values* must never appear in an error surfaced to a
# client, a log file, or a database row. Matched on the NAME, so a new secret picks
# up redaction automatically as long as it follows the house naming convention.
#
# Deliberately NOT matching `URL$`. Credential-bearing URLs (MONGO_URL, DATABASE_URL)
# are already handled by the URI pass above, which redacts only the password and
# keeps the host — whereas matching on the name would blank the entire value. The
# rest (FRONTEND_URL, API_URL, APP_URL) are public endpoints, and redacting them
# turned ordinary log lines into "***FRONTEND_URL***/dashboard" for no benefit.
_SECRET_NAME_RE = re.compile(r"(SECRET|PASSWORD|TOKEN|CREDENTIAL|DSN|_KEY)$")

# Stripe's publishable key is designed to ship to browsers; redacting it is noise.
# Anything else carrying PUBLIC/PUBLISHABLE in the name is non-secret by convention.
_PUBLIC_NAME_RE = re.compile(r"PUBLISHABLE|PUBLIC")

# Below this length a "secret" is too short to redact safely — blanket-replacing a
# 3-character value would mangle unrelated text and make errors unreadable.
_MIN_REDACTABLE_SECRET_LEN = 8


def redact_secrets(text: Any) -> str:
    """Strip credentials out of subprocess/driver output before it is exposed.

    Child-process stderr is captured and then (a) returned in an HTTP error body,
    (b) written to a log file, and (c) persisted to Mongo. Any of those is an
    exfiltration path for whatever the child happened to print — and the scraper
    subprocesses call `load_dotenv()` themselves, so they hold the full secret set
    regardless of the environment the parent passes them. A pymongo/asyncpg
    connection failure prints the connection URI *including the password*, which is
    the concrete case this guards.

    Two passes: connection-URI credentials by pattern (catches secrets this process
    may not even hold), then exact-value replacement for every secret-looking
    environment variable (catches a library dumping its config).

    Returns "" for None/empty so callers can use the result unconditionally.

    FAILS CLOSED. If anything in here raises, the original text is NOT returned —
    a redactor that falls back to its unredacted input on error is worse than no
    redactor, because callers trust the result enough to write it to a log file and
    a database row. Today the callers only pass `str` (subprocess is invoked with
    `text=True`, and `str(exc)` is a string), but a future `text=False` would hand
    this bytes and raise `TypeError` from inside an exception handler; bytes are
    therefore decoded and any other type coerced rather than blowing up.
    """
    if text is None or text == "":
        return ""

    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        elif not isinstance(text, str):
            text = str(text)

        redacted = _URI_CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:***@", text)

        for name, value in os.environ.items():
            if not value or len(value) < _MIN_REDACTABLE_SECRET_LEN:
                continue
            if not _SECRET_NAME_RE.search(name) or _PUBLIC_NAME_RE.search(name):
                continue
            if value in redacted:
                redacted = redacted.replace(value, f"***{name}***")

        return redacted
    except Exception:
        # Deliberately no `raise`: this is called from inside except-blocks that are
        # already handling a failure, and propagating would turn a handled scraper
        # error into an unhandled 500.
        logger.exception("redact_secrets failed; withholding the unredacted text")
        return "[redaction failed — output withheld]"


_REQUEST_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _safe_request_id(value: Any) -> str | None:
    """Return a header-safe request identifier, or None when invalid."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _REQUEST_ID_RE.fullmatch(candidate) else None


def get_request_id(request: Request) -> str:
    """Return one bounded, header-safe correlation ID for the whole request.

    IDs supplied by a proxy or client are accepted only when they use the
    documented safe character set and fit within 128 characters. Invalid or
    missing values are replaced with a UUID, preventing response-header
    injection and unbounded audit/log fields.
    """
    candidates = (
        getattr(request.state, "request_id", None),
        request.headers.get("X-Request-ID"),
        request.headers.get("X-Correlation-ID"),
    )
    request_id = next(
        (safe for value in candidates if (safe := _safe_request_id(value)) is not None),
        str(uuid.uuid4()),
    )
    request.state.request_id = request_id
    return request_id


def _normalise_detail(detail: Any, *, status_code: int) -> tuple[str | None, str | None, bool | None, dict[str, Any] | None]:
    """Generated function header.

    Function: _normalise_detail
    Path: backend/utils/error_response.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if status_code >= 500:
        # Existing routers sometimes raise HTTPException(503, str(exc)).
        # Never echo provider, database, or stack details for server-side failures.
        return None, None, None, None
    if isinstance(detail, dict):
        code = detail.get("code") or detail.get("error")
        message = detail.get("message") or detail.get("detail")
        retryable = detail.get("retryable")
        metadata = {k: v for k, v in detail.items() if k not in {"code", "error", "message", "detail", "retryable"}}
        return code, message, retryable, metadata or None
    if isinstance(detail, str):
        return None, detail, None, None
    return None, None, None, None


def build_error_payload(
    request: Request,
    *,
    status_code: int,
    code: str | None = None,
    message: str | None = None,
    detail: Any = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: build_error_payload
    Path: backend/utils/error_response.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    detail_code, detail_message, detail_retryable, metadata = _normalise_detail(detail, status_code=status_code)
    resolved_code = code or detail_code or STATUS_CODES.get(status_code, "UNKNOWN_ERROR")
    resolved_message = message or detail_message or SAFE_STATUS_MESSAGES.get(status_code, SAFE_STATUS_MESSAGES[500])
    resolved_retryable = retryable if retryable is not None else detail_retryable
    if resolved_retryable is None:
        resolved_retryable = status_code in {429, 500, 502, 503, 504}

    error: dict[str, Any] = {
        "code": resolved_code,
        "message": resolved_message,
        "status": status_code,
        "request_id": get_request_id(request),
        "retryable": bool(resolved_retryable),
    }
    if metadata:
        error["metadata"] = metadata
    return {"error": error}


def build_error_response(
    request: Request,
    *,
    status_code: int,
    code: str | None = None,
    message: str | None = None,
    detail: Any = None,
    retryable: bool | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Generated function header.

    Function: build_error_response
    Path: backend/utils/error_response.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payload = build_error_payload(
        request,
        status_code=status_code,
        code=code,
        message=message,
        detail=detail,
        retryable=retryable,
    )
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = payload["error"]["request_id"]
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload), headers=response_headers)


def log_unhandled_exception(request: Request, exc: Exception) -> None:
    """Generated function header.

    Function: log_unhandled_exception
    Path: backend/utils/error_response.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    request_id = get_request_id(request)
    logger.exception(
        "Unhandled API exception request_id=%s method=%s path=%s type=%s",
        request_id,
        request.method,
        request.url.path,
        type(exc).__name__,
    )
