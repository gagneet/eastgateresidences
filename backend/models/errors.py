"""
Structured error models for Trust Accounting — Phase 2.

Error contracts distinguish five classes:
  validation  — caller sent bad data
  permission  — caller lacks the required role
  conflict    — state collision (duplicate, period locked, already closed)
  state       — entity is not in the right lifecycle state
  internal    — unexpected server-side failure

Usage:
    raise TrustPeriodLockedError(period="2026-03", locked_by="alice@example.com")
    # → HTTP 409 with TrustError body
"""
from pydantic import BaseModel
from typing import Any, Dict, Literal, Optional

# ─── Canonical error codes ────────────────────────────────────────────────────

PERIOD_LOCKED = "PERIOD_LOCKED"
DUPLICATE_POSTING = "DUPLICATE_POSTING"
IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
PERMISSION_DENIED = "PERMISSION_DENIED"
INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
CROSS_BUILDING_ACCESS = "CROSS_BUILDING_ACCESS"
UNBALANCED_POSTING = "UNBALANCED_POSTING"
MIGRATION_COMMIT_FAILED = "MIGRATION_COMMIT_FAILED"
RECONCILIATION_ALREADY_CLOSED = "RECONCILIATION_ALREADY_CLOSED"
PERIOD_HAS_OPEN_ITEMS = "PERIOD_HAS_OPEN_ITEMS"
DUPLICATE_STATEMENT = "DUPLICATE_STATEMENT"

TrustErrorCode = Literal[
    "PERIOD_LOCKED",
    "DUPLICATE_POSTING",
    "IDEMPOTENT_REPLAY",
    "PERMISSION_DENIED",
    "INVALID_STATE_TRANSITION",
    "CROSS_BUILDING_ACCESS",
    "UNBALANCED_POSTING",
    "MIGRATION_COMMIT_FAILED",
    "RECONCILIATION_ALREADY_CLOSED",
    "PERIOD_HAS_OPEN_ITEMS",
    "DUPLICATE_STATEMENT",
]

# ─── Error classes ────────────────────────────────────────────────────────────

VALIDATION = "validation"
PERMISSION = "permission"
CONFLICT = "conflict"
STATE = "state"
INTERNAL = "internal"

TrustErrorClass = Literal["validation", "permission", "conflict", "state", "internal"]


# ─── Structured error payload ─────────────────────────────────────────────────

class TrustError(BaseModel):
    """Machine-readable error body returned for all trust accounting errors."""

    error_code: TrustErrorCode
    """One of the canonical UPPER_SNAKE constants above."""

    error_class: TrustErrorClass
    """validation | permission | conflict | state | internal"""

    message: str
    """Human-readable description — safe to display in UI."""

    context: Dict[str, Any] = {}
    """Structured extra data relevant to the error (period, building_id, etc.)."""

    request_id: Optional[str] = None
    """Propagated from X-Request-ID header when available."""


# ─── Domain exceptions ────────────────────────────────────────────────────────

class TrustPeriodLockedError(Exception):
    """Raised when a posting targets a locked financial period."""

    def __init__(
            self,
            period: str,
            locked_by: Optional[str] = None,
            locked_at: Optional[str] = None,
    ) -> None:
        """Generated function header.

        Function: TrustPeriodLockedError.__init__
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.period = period
        self.locked_by = locked_by
        self.locked_at = locked_at
        super().__init__(f"Period {period!r} is locked.")

    def to_trust_error(self, request_id: Optional[str] = None) -> TrustError:
        """Generated function header.

        Function: TrustPeriodLockedError.to_trust_error
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return TrustError(
            error_code=PERIOD_LOCKED,
            error_class=CONFLICT,
            message=f"Period {self.period!r} is locked and cannot accept new postings.",
            context={
                "period": self.period,
                "locked_by": self.locked_by,
                "locked_at": self.locked_at,
            },
            request_id=request_id,
        )


class TrustStateConflictError(Exception):
    """Raised when an operation is invalid given the current entity state."""

    def __init__(
            self,
            entity: str,
            entity_id: str,
            current_state: str,
            required_state: str,
    ) -> None:
        """Generated function header.

        Function: TrustStateConflictError.__init__
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.entity = entity
        self.entity_id = entity_id
        self.current_state = current_state
        self.required_state = required_state
        super().__init__(
            f"{entity} {entity_id!r} is in state {current_state!r}; "
            f"required: {required_state!r}."
        )

    def to_trust_error(self, request_id: Optional[str] = None) -> TrustError:
        """Generated function header.

        Function: TrustStateConflictError.to_trust_error
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return TrustError(
            error_code=INVALID_STATE_TRANSITION,
            error_class=STATE,
            message=(
                f"Cannot perform this operation on {self.entity} "
                f"in state {self.current_state!r}."
            ),
            context={
                "entity": self.entity,
                "entity_id": self.entity_id,
                "current_state": self.current_state,
                "required_state": self.required_state,
            },
            request_id=request_id,
        )


class MigrationStateError(Exception):
    """Raised when an MRI migration operation requires a specific pipeline state."""

    def __init__(self, migration_id: str, current_state: str, required_state: str) -> None:
        """Generated function header.

        Function: MigrationStateError.__init__
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.migration_id = migration_id
        self.current_state = current_state
        self.required_state = required_state
        super().__init__(
            f"Migration {migration_id!r}: state is {current_state!r}, "
            f"expected {required_state!r}."
        )

    def to_trust_error(self, request_id: Optional[str] = None) -> TrustError:
        """Generated function header.

        Function: MigrationStateError.to_trust_error
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return TrustError(
            error_code=MIGRATION_COMMIT_FAILED,
            error_class=STATE,
            message=(
                f"Migration operation failed: migration is in state "
                f"{self.current_state!r}, expected {self.required_state!r}."
            ),
            context={
                "migration_id": self.migration_id,
                "current_state": self.current_state,
                "required_state": self.required_state,
            },
            request_id=request_id,
        )


class TrustPeriodHasOpenItemsError(Exception):
    """Raised when attempting to unlock/close a period that still has unmatched transactions."""

    def __init__(self, period: str, open_count: int) -> None:
        """Generated function header.

        Function: TrustPeriodHasOpenItemsError.__init__
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.period = period
        self.open_count = open_count
        super().__init__(
            f"Period {period!r} has {open_count} unmatched transaction(s)."
        )

    def to_trust_error(self, request_id: Optional[str] = None) -> TrustError:
        """Generated function header.

        Function: TrustPeriodHasOpenItemsError.to_trust_error
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return TrustError(
            error_code=PERIOD_HAS_OPEN_ITEMS,
            error_class=CONFLICT,
            message=(
                f"Period {self.period!r} cannot be unlocked: "
                f"{self.open_count} unmatched transaction(s) remain."
            ),
            context={
                "period": self.period,
                "open_count": self.open_count,
            },
            request_id=request_id,
        )


class DuplicateStatementError(Exception):
    """Raised when a bank statement import is submitted with a checksum that already exists."""

    def __init__(self, checksum: str, existing_import_run_id: str) -> None:
        """Generated function header.

        Function: DuplicateStatementError.__init__
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.checksum = checksum
        self.existing_import_run_id = existing_import_run_id
        super().__init__(
            f"Statement with checksum {checksum!r} already imported "
            f"(import_run_id={existing_import_run_id!r})."
        )

    def to_trust_error(self, request_id: Optional[str] = None) -> TrustError:
        """Generated function header.

        Function: DuplicateStatementError.to_trust_error
        Path: backend/models/errors.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return TrustError(
            error_code=DUPLICATE_STATEMENT,
            error_class=CONFLICT,
            message="This bank statement has already been imported.",
            context={
                "checksum": self.checksum,
                "existing_import_run_id": self.existing_import_run_id,
            },
            request_id=request_id,
        )
