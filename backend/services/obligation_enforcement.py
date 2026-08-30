# @featuretrace:scoped-capability-access — Carry a Decision's obligations to the response.
# Layer: service
# Data flow: require_capability -> Decision -> request.state -> middleware -> masked JSON response (scope param: building|global).
# Related: backend/services/capability_registry.py
#          backend/services/field_masking.py
#          docs/security/acl_information_access_implementation_plan.md §5
# Tests: tests/backend/test_obligation_enforcement.py

"""Make a ``Decision``'s obligations actually reach the response body.

## The gap this closes

``decide()`` has returned populated ``obligations`` since Phase 3, and
``field_masking.apply_obligations()`` has been tested since Phase 4. Between
them sat nothing: ``require_capability`` computed the decision, raised on a
denial, and returned the user — dropping the obligations on the floor. Every
capability-guarded route therefore returned owner contact details, per-lot
arrears and supplier bank fields in full, exactly as if masking did not exist.

This module is the missing carrier. The decision is recorded on
``request.state`` when it is made, and applied to the response body on the way
out.

## Why a middleware and not a per-route call

Two reasons, and the second one is the one that decides it.

First, the plan is explicit that masking happens in one place and never per
route (§5): a rule applied in twenty serialisers drifts, and every drift is in
the disclosing direction. A route that forgets to mask fails open and looks
completely normal in review.

Second — and this is a mechanical constraint, not a preference — masking cannot
be done *inside* the handler on a route that declares a ``response_model``.
FastAPI validates the handler's return value against that model before
serialising it. ``WITHHELD`` is the string ``"__withheld__"``, so masking a
``net_balance: float`` field before returning would raise a
``ResponseValidationError`` and turn a privacy control into a 500. The mask must
therefore be applied to the already-serialised JSON, which is what this
middleware does.

Routes that build their payload by hand and return a bare ``dict`` may still
mask explicitly via :func:`apply_pending_obligations` — useful when the route
also holds a per-resource ``field_mask`` from ``core.resource_access_grants``,
which the middleware cannot know about. Doing so marks the request handled so
the middleware does not walk the payload a second time.

## Fail-closed edges

- Obligations from multiple decisions on one request are **unioned**, never
  intersected. Two capabilities checked means the stricter mask applies.
- **If the mask cannot be applied, the whole response is withheld.** A body that
  is unbuffered, over ``MAX_MASKABLE_BODY_BYTES``, not valid JSON, or not a JSON
  media type at all is refused with a 413/500 carrying no payload.

  These four branches originally passed the body through unmasked and logged a
  warning. That was a fail-open bypass, and a reachable one: a caller who has
  legitimately passed the capability check but is subject to a field mask could
  request an enormous page and receive the per-lot arrears the access matrix
  reserves for the treasurer. Raised in review of PR #672 and correct.

  A guarded route that legitimately returns a large payload, a CSV export or a
  stream must mask at the source and call :func:`mark_obligations_applied`,
  which suppresses this middleware for that request. Refusing is deliberately
  noisy — it surfaces in development, where the route can be paginated or taught
  to mask, rather than in production as a silent disclosure.

- Refusals are **returned, not raised**. An ``HTTPException`` raised inside a
  ``BaseHTTPMiddleware`` is not converted into a response — Starlette's
  ``ExceptionMiddleware`` sits *inside* the middleware stack, so the exception
  propagates out of the application and surfaces as an unhandled 500 with no
  usable status or detail. Verified directly rather than assumed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.field_masking import apply_obligations

logger = logging.getLogger(__name__)

#: Attribute names on ``request.state``. Namespaced so they cannot collide with
#: the request-context building/tenant attributes set elsewhere.
_OBLIGATIONS_ATTR = "authorisation_obligations"
_DECISIONS_ATTR = "authorisation_decision_ids"
_APPLIED_ATTR = "authorisation_obligations_applied"

#: Above this the response is REFUSED with a 413, not passed through. Chosen so
#: an ordinary list endpoint fits comfortably while a bulk export cannot quietly
#: consume the worker's memory inside a middleware. A capability-guarded route
#: that legitimately returns more than this should paginate, or mask at the
#: source and call ``mark_obligations_applied``.
MAX_MASKABLE_BODY_BYTES = 8 * 1024 * 1024

#: Content types this middleware can parse and mask. A guarded route returning
#: anything else is REFUSED unless it masked at the source — an export is where
#: bulk PII leaves a building, so passing it through would be the largest hole.
JSON_MEDIA_TYPES = ("application/json", "application/problem+json")


# ── Recording ────────────────────────────────────────────────────────────────

def record_obligations(
    request: Request | None,
    obligations: Iterable[str],
    *,
    decision_id: str = "",
) -> None:
    """Attach one decision's obligations to the request that produced it.

    Called by ``require_capability``'s dependency as soon as the decision is
    made. Obligations accumulate across decisions by union: if a route checks
    two capabilities, the response must satisfy both masks, and the union is the
    stricter of the two.
    """
    if request is None:
        return
    state = request.state
    existing: set[str] = set(getattr(state, _OBLIGATIONS_ATTR, ()) or ())
    existing.update(str(item) for item in (obligations or ()) if item)
    setattr(state, _OBLIGATIONS_ATTR, tuple(sorted(existing)))

    if decision_id:
        ids: list[str] = list(getattr(state, _DECISIONS_ATTR, ()) or ())
        ids.append(str(decision_id))
        setattr(state, _DECISIONS_ATTR, tuple(ids))


def pending_obligations(request: Request | None) -> tuple[str, ...]:
    """Return the union of obligations recorded on this request so far."""
    if request is None:
        return ()
    return tuple(getattr(request.state, _OBLIGATIONS_ATTR, ()) or ())


def decision_ids(request: Request | None) -> tuple[str, ...]:
    """Return the decision ids recorded on this request, oldest first.

    Exists so the audit write path in GAP-SEC-003 can correlate a response with
    every decision that shaped it, without re-running the evaluator.
    """
    if request is None:
        return ()
    return tuple(getattr(request.state, _DECISIONS_ATTR, ()) or ())


def mark_obligations_applied(request: Request | None) -> None:
    """Record that the route already masked its own payload.

    The middleware skips a request marked this way. Use it only after actually
    applying the obligations — marking without masking disables the floor for
    that request.
    """
    if request is not None:
        setattr(request.state, _APPLIED_ATTR, True)


def obligations_applied(request: Request | None) -> bool:
    """Generated function header.

    Function: obligations_applied
    Path: backend/services/obligation_enforcement.py
    """
    return bool(request is not None and getattr(request.state, _APPLIED_ATTR, False))


# ── Explicit in-route application ────────────────────────────────────────────

def apply_pending_obligations(
    request: Request | None,
    payload: Any,
    *,
    extra_fields: Iterable[str] = (),
) -> Any:
    """Mask ``payload`` with this request's obligations and mark it handled.

    For routes that return a bare ``dict``/``list`` (no ``response_model``) and
    want to combine the class-level mask with a per-resource ``field_mask`` from
    ``core.resource_access_grants``, which the middleware has no way to know
    about.

    Do **not** call this from a route that declares a ``response_model``: FastAPI
    validates the return value against the model before serialising, and the
    ``WITHHELD`` sentinel is a string, so masking a numeric or datetime field
    would raise ``ResponseValidationError``. Let the middleware handle those.
    """
    obligations = pending_obligations(request)
    if not obligations and not tuple(extra_fields or ()):
        return payload
    masked = apply_obligations(payload, obligations, extra_fields=extra_fields)
    mark_obligations_applied(request)
    return masked


# ── The floor ────────────────────────────────────────────────────────────────

class ObligationEnforcementMiddleware(BaseHTTPMiddleware):
    """Apply recorded field-masking obligations to outgoing JSON responses.

    This is the enforcement floor described in the plan §5: a route cannot
    forget to mask, because masking does not happen in the route.

    The middleware is inert for any request that never made an authorisation
    decision — which today is most of them — so it costs one attribute lookup on
    an unguarded path. It only buffers a body when obligations are actually
    pending.
    """

    async def dispatch(self, request: Request, call_next):  # noqa: D102 - see class docstring
        response = await call_next(request)

        obligations = pending_obligations(request)
        if not obligations or obligations_applied(request):
            return response

        # Only successful JSON payloads carry maskable fields. An error body is
        # generated by us and already withholds detail; masking it would only
        # risk mangling the error contract.
        if response.status_code >= 400:
            return response

        media_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if media_type not in JSON_MEDIA_TYPES:
            # A guarded route returning CSV/PDF/a stream cannot be masked by this
            # middleware, and passing it through would drop the obligations
            # silently — an export is exactly where bulk PII leaves the building.
            # The route must mask at the source and call
            # mark_obligations_applied(); until it does, it is refused.
            return self._refuse(
                request, obligations, status_code=500,
                reason=f"response media type {media_type or 'unknown'!r} cannot be masked",
                remedy="mask at the source and call mark_obligations_applied(request)",
                original=response,
            )

        body = await self._read_body(response)
        if body is None:
            return self._refuse(
                request, obligations, status_code=500,
                reason="response body could not be buffered",
                remedy="return a normal JSON response, or mask at the source",
                original=response,
            )

        if len(body) > MAX_MASKABLE_BODY_BYTES:
            return self._refuse(
                request, obligations, status_code=413,
                reason=f"body {len(body)} bytes exceeds the {MAX_MASKABLE_BODY_BYTES}-byte mask cap",
                remedy="paginate this route, or mask at the source",
                original=response,
            )

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return self._refuse(
                request, obligations, status_code=500,
                reason="content-type claimed JSON but the body did not parse",
                remedy="return valid JSON, or set an accurate content-type",
                original=response,
            )

        masked = apply_obligations(payload, obligations)
        mark_obligations_applied(request)
        return self._rebuild(response, json.dumps(masked, default=str).encode("utf-8"))

    @staticmethod
    def _refuse(
        request: Request,
        obligations: tuple[str, ...],
        *,
        status_code: int,
        reason: str,
        remedy: str,
        original: Response | None = None,
    ) -> Response:
        """Withhold the whole response when the mask could not be applied.

        Fail-closed. Every branch that reaches here had a pending masking
        obligation and no way to honour it, so the body may contain owner contact
        details, per-lot arrears or bank fields. Returning it unmasked would let a
        caller bypass masking by steering a guarded route into one of these states
        — requesting an enormous page, for instance, to receive per-lot arrears
        the access matrix reserves for the treasurer.

        Returned rather than raised: an HTTPException raised inside a
        BaseHTTPMiddleware is NOT converted into a response, because Starlette's
        ExceptionMiddleware sits *inside* the middleware stack. It propagates out
        of the application instead, which is still fail-closed but surfaces as an
        unhandled 500 with no usable status or detail. Verified, not assumed.

        The client message deliberately says nothing about which fields were being
        withheld — the same non-disclosure rule assert_capability() follows.
        """
        logger.error(
            "obligation enforcement FAILED — withholding response: %s "
            "path=%s obligations=%s remedy=%s",
            reason, request.url.path, ",".join(obligations), remedy,
        )

        # Carry the original response's headers forward. This middleware is
        # registered OUTSIDE CORSMiddleware, so by the time control reaches here
        # the CORS headers are already on the response being replaced. Building a
        # bare JSONResponse dropped them, and a browser then reported an opaque
        # CORS failure instead of the 413/500 — the caller could not tell a
        # withheld response from a network error. Content framing is excluded
        # because the body is different.
        headers = {
            key: value
            for key, value in (original.headers.items() if original is not None else ())
            if key.lower() not in {"content-length", "content-type"}
        }
        return JSONResponse(
            status_code=status_code,
            content={"detail": "Response withheld: field masking could not be applied."},
            headers=headers or None,
        )

    @staticmethod
    async def _read_body(response: Response) -> bytes | None:
        """Collect a response body, returning None if it cannot be buffered."""
        if hasattr(response, "body") and isinstance(getattr(response, "body"), (bytes, bytearray)):
            return bytes(response.body)
        iterator = getattr(response, "body_iterator", None)
        if iterator is None:
            return None
        chunks: list[bytes] = []
        total = 0
        async for chunk in iterator:
            piece = chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8")
            total += len(piece)
            chunks.append(bytes(piece))
            # Keep reading past the cap so the connection is drained, but stop
            # accumulating once the body is already too large to mask.
            if total > MAX_MASKABLE_BODY_BYTES * 2:
                break
        return b"".join(chunks)

    @staticmethod
    def _rebuild(original: Response, body: bytes) -> Response:
        """Return a Response carrying ``body`` with the original's status/headers.

        ``content-length`` is recomputed: masking changes the body length, and a
        stale length truncates the response at the client.
        """
        headers = {
            key: value
            for key, value in original.headers.items()
            if key.lower() != "content-length"
        }
        return Response(
            content=body,
            status_code=original.status_code,
            headers=headers,
            media_type=original.media_type,
        )
