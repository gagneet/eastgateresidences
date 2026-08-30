# @featuretrace:cutover-toggle-safety — pin which datastore a dispatched route uses in tests.
# Layer: test
# Data flow: test module → pin_store() → store_router.require_domain_source (global).
# Related: backend/services/store_router.py
#          tests/backend/test_finance_pg_wiring.py
"""Force a dispatched route to one datastore for the duration of a test.

WHY EVERY CONVERTED ROUTE NEEDS THIS
------------------------------------
Once a route goes through `store_router.read_through()`, a test that patches
`routers.<module>.db` is only HALF mocked. The handler asks the cutover control plane
which store to use; on a developer machine or CI box where `finance_ledger` is promoted
the answer is PostgreSQL, so the handler queries the real database and the carefully
built mock is never touched.

It does not fail loudly. It fails as a wrong number:

    test_get_expenses_returns_list      assert 2 == 33
    test_future_levy_year_excluded      assert ['2026'..'2021'] == ['2026','2025','2024']

Both look like the assertion is wrong. Neither is - the handler simply answered from a
different store than the test set up. This is the read-side form of footgun #20 ("when
mocking a handler, mock EVERY store it touches"), and it will happen to every existing
test of every route as that route is wired.

WHICH STORE TO PIN
------------------
Pin the store whose BEHAVIOUR the test is about:

  * testing a Mongo-side fallback, a document shape, or a filter that runs in Python
    regardless of source -> pin_store("mongo")
  * testing that PostgreSQL is preferred, or a PostgreSQL reader's output
    -> pin_store("postgres")

Pinning is not a workaround. It makes the test state which path it exercises instead of
inheriting whatever this machine's control plane happens to say, which is the difference
between a test that means something and one that passes by coincidence.

Usage:
    from fixtures.store_pinning import pin_store

    with pin_store("mongo"), patch("routers.finance.db", mock_db):
        result = await get_expenses(year="2026", ...)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch


@dataclass(frozen=True)
class _PinnedDecision:
    """Minimal stand-in for domain_source_guard's DomainSourceDecision.

    Only the three attributes `store_router.resolve_store` reads are provided. Building
    a fuller fake would couple every test to the guard's internal shape, so that a field
    added there breaks tests that never cared about it.
    """

    source: object
    shadow_enabled: bool = False
    blocked_reason: str | None = None


@contextmanager
def pin_store(source: str = "mongo"):
    """Pin `read_through`'s dispatch to one store for the duration of the block.

    Patches `require_domain_source` rather than `resolve_store`, so the code under test
    still exercises the real resolve_store wrapper - its DataSource unwrapping, its
    mirror_to_mongo derivation and its exception handling all still run. Patching
    resolve_store itself would stub out the very seam these tests exist to cover.
    """
    if source not in ("mongo", "postgres"):
        raise ValueError(f"pin_store expects 'mongo' or 'postgres', got {source!r}")

    from models.cutover_status import DataSource
    from services import store_router

    resolved = DataSource.postgres if source == "postgres" else DataSource.mongo
    with patch.object(
        store_router,
        "require_domain_source",
        new=AsyncMock(return_value=_PinnedDecision(source=resolved)),
    ):
        yield
