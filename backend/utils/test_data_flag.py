# @featuretrace:cutover-toggle-safety — canonical owner of "is this write happening under a test".
# Layer: service
# Data flow: identity_repo.create_user + cutover_status_service.record_shadow_diff →
#            under_pytest() → is_test_data on the written row (global).
# Related: backend/db_postgres/repos/identity_repo.py
#          backend/services/cutover_status_service.py
#          docs/architecture/canonical_owners.yaml (concept: test-data-flag)
# Tests: tests/backend/test_test_data_flag.py
"""Is this process currently executing a pytest test?

`is_test_data` defends nothing unless something sets it. The conftest sweep and the
`APP_ENV=production` login gate both key off that flag, so an UNFLAGGED row written
during a test run is invisible to both — a live credential or, for shadow diffs, a
permanent entry against a production cutover gate.

The static guard in `tests/backend/test_no_unflagged_test_users.py` cannot catch this
class: it scans `tests/` for direct writes, but a test can reach a production writer
without writing the call itself — it exercises a handler, mocks that handler's Mongo
collections thoroughly, and never notices the handler ALSO writes to Postgres, which
has no test double and so hits the real `DATABASE_URL`.

Two real incidents this backstops:

* 2026-08-27 — `owner@test.com`, `owner2@test.com` and `owner@example.com` became live,
  active, password-bearing rows in East Gate's production tenant with `is_test_data`
  FALSE, via `routers.auth.register`'s unmocked Postgres write.
* 2026-08-29 — 260 unresolved `core.shadow_diffs` rows against building 13195, all
  `is_test_data=False`, carrying obvious fixture payloads (`total_expense` of `12345`
  cents against a real PG total of `14565265`). They blocked the finance read gate
  while describing nothing about production data. See
  `services/finance_shadow_read_service.py`'s scope guard for the other half of that fix.

Keyed on ``PYTEST_CURRENT_TEST``, which pytest sets per-test and clears afterwards, so
a production process can never take this branch.
"""
from __future__ import annotations

import os


def under_pytest() -> bool:
    """True while a pytest test is executing in this process."""
    return "PYTEST_CURRENT_TEST" in os.environ
