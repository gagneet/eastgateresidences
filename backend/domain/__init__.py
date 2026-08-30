"""
backend/domain — Domain model package.

# @featuretrace:financial_integration_v2 — domain root; Cents type used by all financial models.
# Layer: domain
# Data flow: all financial writes → Pydantic domain models (this package) → MongoDB/Postgres financial stores (building-scoped).
# Related: backend/domain/journals.py
#          backend/domain/period_lifecycle.py
#          backend/domain/reconciliation.py

The domain layer is the authoritative source of financial truth. Two rules enforced here:

  1. Cents = integer-cent money. `float` is BANNED in this package tree.
     Enforced by tests/backend/test_no_floats_in_domain.py.

  2. Providers (Basiq, DEFT, Monoova, Textract) NEVER appear here.
     Envelope translation happens in backend/integrations/; only EventEnvelope
     instances cross into domain code.
"""
from typing import Annotated

from pydantic import Field

# Integer-only money type. Every monetary field in domain models must use this.
# Display formatting (e.g. "$1,234.56") is the frontend's responsibility.
# Coercion from provider floats happens in backend/integrations/adapters/ only.
Cents = Annotated[int, Field(ge=-(10 ** 15), le=10 ** 15)]
