"""Timestamp coercion for API response models.

WHY THIS EXISTS
Mongo stores BSON dates natively, so whether a document's `created_at` comes back as
a `str` or a `datetime` depends entirely on which writer produced it. This codebase
has both conventions: most writers call `.isoformat()` before inserting, but several
— including `seeds/demo_customer.py` — insert `datetime` objects directly.

Response models declare `created_at: str` in 139 places. Any one of them served a
document written the other way raises `ResponseValidationError`, which FastAPI turns
into a **500**, not a 422 — so it reads as a server fault with no clue about the
cause.

That is not hypothetical. On 2026-08-26 a k6 run of the owner-dashboard benchmark
found `GET /annual-levies` and `GET /workflow-requests` returning 500 on every single
request for the demo building — which is the sales demo. Ten of its collections carry
BSON datetimes.

Coercing on READ rather than patching 139 declarations means existing documents are
fixed without a data migration, and a future writer using the other convention cannot
re-break the endpoint. Seeds are being fixed too, but a read path should not depend on
every writer having got it right.

`Z` rather than `+00:00` for UTC: the frontend parses these with `new Date(...)`,
which handles both, but the rest of the API emits `Z` and a response should not be
internally inconsistent about it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Optional

from pydantic import BeforeValidator


def _to_iso_string(value: Any) -> Any:
    """Render a datetime/date as an ISO-8601 string; pass everything else through.

    Non-temporal values are returned untouched so that pydantic still reports a real
    type error for genuinely wrong input — this normalises a known representational
    split, it does not silence validation.
    """
    if isinstance(value, datetime):
        text = value.isoformat()
        # A naive datetime is stored by Mongo as UTC, so label it as such rather than
        # emitting a timestamp whose zone the client has to guess.
        return text.replace("+00:00", "Z") if value.tzinfo else f"{text}Z"
    if isinstance(value, date):
        return value.isoformat()
    return value


#: A string timestamp that also accepts a `datetime`/`date` and renders it ISO-8601.
IsoTimestamp = Annotated[str, BeforeValidator(_to_iso_string)]

#: Optional variant. `None` passes straight through.
OptionalIsoTimestamp = Annotated[Optional[str], BeforeValidator(_to_iso_string)]


# Sorting a mixed list is the OTHER half of the same problem, and the coercion above
# does not reach it.
#
# `activities.sort(key=lambda x: x.get("created_at", ""))` in routers/analytics.py
# raised `TypeError: '<' not supported between instances of 'datetime.datetime' and
# 'str'` — a 500 on GET /analytics/activities for every request, found by the same k6
# run. The list is assembled from several collections plus some synthesised entries,
# so it holds datetimes from one writer, ISO strings from another, and the `""`
# default for rows missing the field entirely. Any two of those three are
# incomparable.
#
# A naive `str(...)` key would not fix it either: it makes the comparison legal while
# ordering "2026-08-20 22:16:45" against "2026-08-20T22:16:45Z" by ASCII, where the
# space sorts before the T and the feed silently comes out in the wrong order. Wrong
# order is worse than a 500 — the 500 is at least visible.
_SORTS_LAST = datetime.min.replace(tzinfo=timezone.utc)


def timestamp_sort_key(value: Any) -> datetime:
    """A single comparable type for any timestamp shape a document might hold.

    Returns an aware UTC datetime. Naive values are treated as UTC, which is what
    Mongo stored them as.

    Missing or unparseable values return the floor, so with `reverse=True` — how every
    "newest first" feed sorts — they land at the END rather than the top. An absent
    timestamp should not push a row to the front of a feed.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return _SORTS_LAST
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _SORTS_LAST
