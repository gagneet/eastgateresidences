# Phase 2 API Corrections & Bug Fixes

**Scope:** `backend/services/reconciliation_matching_service.py`,
`backend/services/trust_posting_service.py`,
`backend/routers/investor_intelligence.py`,
`backend/routers/mri_migration.py`
**Phase:** 2 — Production Hardening
**Status:** Implemented

---

## Overview

During Phase 2 implementation, several discrepancies were identified between the
original architecture specifications and the correct implemented behaviour. This
document records each correction as the authoritative reference for tests, API clients,
and future development.

---

## 1. Grade D Evaluated Before Grade C in `compute_investor_grade()`

### Problem

The original spec described grades in A → B → C → D order, which implies Grade C is
evaluated before Grade D. However, the C condition (`reserve < 75%`) fully overlaps the
D condition (`reserve < 50%`). Evaluating C first means a building with critically
under-funded reserves (e.g. 40%) would incorrectly receive a C instead of a D.

### Fix

`compute_investor_grade()` in `backend/routers/investor_intelligence.py` evaluates
Grade D **before** Grade C:

```python
# A
if stress_score < 25 and reserve_adequacy_pct > 100 and arrears_rate_pct < 5 and compliance_score_pct > 90:
    grade = "A"

# B
elif stress_score < 50 and reserve_adequacy_pct > 75 and arrears_rate_pct < 10:
    grade = "B"

# D — checked BEFORE C to prevent severe under-funding being masked by the C threshold
elif stress_score >= 75 or reserve_adequacy_pct < 50 or arrears_rate_pct > 20:
    grade = "D"

# C
elif stress_score < 75 and (reserve_adequacy_pct < 75 or 10 <= arrears_rate_pct <= 20):
    grade = "C"
```

### Authoritative Grade Thresholds

| Grade | Meaning   | Trigger Conditions                                                      |
|-------|-----------|-------------------------------------------------------------------------|
| A     | Excellent | stress < 25 AND reserve > 100% AND arrears < 5% AND compliance > 90%    |
| B     | Good      | stress < 50 AND reserve > 75% AND arrears < 10%                         |
| D     | High Risk | stress >= 75 OR reserve < 50% OR arrears > 20% — **evaluated before C** |
| C     | Caution   | stress < 75 AND (reserve < 75% OR 10% <= arrears <= 20%)                |

### Test Impact

Tests that assert Grade C for a building with `reserve_adequacy_pct < 50%` are wrong
and must be updated to expect Grade D.

---

## 2. `THRESHOLD_EXACT = 0.80` in `MatchingEngine`

### Problem

The architecture document stated `score >= 0.90` triggers an `exact` match. The actual
maximum achievable score in `score_pair()` is **0.80**, making 0.90 unreachable.

### Why the Maximum Is 0.80

The five signals and their weights are:

| Signal                   | Weight | Mutual Exclusion                             |
|--------------------------|--------|----------------------------------------------|
| `exact_amount_match`     | 0.40   | Cannot combine with `amount_tolerance_match` |
| `amount_tolerance_match` | 0.20   | Cannot combine with `exact_amount_match`     |
| `date_proximity`         | 0.20   | Scales by proximity (1.0 same day)           |
| `reference_similarity`   | 0.15   | Scales by Levenshtein ratio                  |
| `description_similarity` | 0.05   | Scales by Jaccard token overlap              |

The best possible non-exclusive combination is:

```
exact_amount (0.40) + same_date (0.20 × 1.0) + reference_match (0.15 × 1.0) + description_overlap (0.05 × 1.0) = 0.80
```

### Correct Thresholds

```python
THRESHOLD_EXACT  = 0.80   # highest achievable score is "exact"
THRESHOLD_LIKELY = 0.70
THRESHOLD_WEAK   = 0.50
```

### Test Impact

`test_exact_match_same_day_high_score` should assert `confidence_score >= 0.80`.
Any assertion of `confidence_score >= 0.90` will always fail because the score is
bounded by `min(1.0, score)` and 0.80 is the practical ceiling.

---

## 3. `score_pair()` Building Isolation Guard

### Behaviour

`MatchingEngine.score_pair()` enforces building-level isolation at the pair level.
When both `bank_line` and `internal_tx` carry a `building_id` field and those values
differ, `score_pair()` **immediately returns** with `confidence_score = 0.0`,
`match_type = "none"`, and `suggested_action = "reject"`:

```python
bl_building = bank_line.get("building_id")
tx_building = internal_tx.get("building_id")
if bl_building and tx_building and bl_building != tx_building:
    return ReconciliationMatch(
        confidence_score=0.0,
        match_type="none",
        match_reasons=["cross_building_mismatch"],
        suggested_action="reject",
    )
```

### Design Rationale

This is a second line of defence. The endpoint-level filter already queries only
records scoped to the JWT building. The guard in `score_pair()` protects against
cases where the candidate generation loop is called with pre-fetched mixed data (e.g.
in unit tests or batch imports).

The error code `CROSS_BUILDING_ACCESS` is defined in `backend/models/errors.py`
for HTTP-level cross-building rejections. `score_pair()` does not raise an exception
— it returns a structured result so the caller can count and log rejections.

---

## 4. Function Signature Reference — Phase 2 Key Functions

### `_compute_idempotency_key` (module-level function)

**File:** `backend/services/trust_posting_service.py`

```python
def _compute_idempotency_key(
    building_id: str,      # 1st — from JWT, never from request body
    unit_number: str,      # 2nd — e.g. "TH017"
    amount_cents: int,     # 3rd — integer cents only, never float
    period: str,           # 4th — e.g. "2026-03"
    source_reference: str, # 5th — DEFT CRN, bank ref, or manual ref
) -> str:                  # returns 64-char hex SHA-256 digest
```

All five arguments are required positional parameters. The function lower-cases and
strips all string fields before hashing. Passing a float for `amount_cents` will
not raise an error here, but will produce a different hash than the equivalent int —
always pass integer cents.

**Called by:** `TrustPostingService.post()` internally. Do not call from routers directly.

---

### `_assert_transition` (module-level function)

**File:** `backend/routers/mri_migration.py`

```python
def _assert_transition(
    current_status: str,  # 1st — current batch status string
    target_status: str,   # 2nd — attempted next status string
    batch_id: str,        # 3rd — included in error message for traceability
) -> None:
```

Raises `HTTP 400` (FastAPI `HTTPException`) if `target_status` is not in
`_VALID_TRANSITIONS[current_status]`. This is **not** a custom `MigrationStateError`;
it raises directly. The error detail includes `batch_id` for log traceability.

The architecture spec stub showed a 2-argument signature — the production implementation
requires the 3rd `batch_id` argument.

---

### `MatchingEngine.score_pair`

**File:** `backend/services/reconciliation_matching_service.py`

```python
def score_pair(
    self,
    bank_line: Dict[str, Any],     # bank statement line document
    internal_tx: Dict[str, Any],   # trust ledger transaction document
) -> ReconciliationMatch:
```

Returns a `ReconciliationMatch` with `confidence_score` in `[0.0, 0.80]` (not `[0.0, 1.0]`
in practice). Cross-building pairs return `confidence_score=0.0` immediately.

---

### `compute_investor_grade`

**File:** `backend/routers/investor_intelligence.py`

```python
def compute_investor_grade(
    stress_score: float,
    reserve_adequacy_pct: float,
    arrears_rate_pct: float,
    compliance_score_pct: float,
) -> tuple[str, list[str]]:
    # Returns (grade, rationale_list)
    # grade is one of: "A", "B", "C", "D"
```

Pure function — no DB access, fully testable. Grade D is evaluated before Grade C.
See Section 1 for the full evaluation order.

---

## 5. `MigrationBatchStatus` — Initial State Is `PENDING`

### Clarification

A newly created migration batch has `status = "pending"`, not `"created"`. Some
earlier design notes and the simplified state machine diagram used `"created"` as the
starting node label; the actual enum value and DB value is `"pending"`.

### Full Enum

```python
class MigrationBatchStatus(str, Enum):
    PENDING           = "pending"          # initial state on batch creation
    VALIDATING        = "validating"
    VALIDATED         = "validated"
    VALIDATION_FAILED = "validation_failed" # terminal
    DRY_RUN           = "dry_run"
    DRY_RUN_COMPLETE  = "dry_run_complete"
    READY_TO_COMMIT   = "ready_to_commit"
    COMMITTING        = "committing"        # transient, set during POST /commit
    COMMITTED         = "committed"
    ROLLBACK_AVAILABLE = "rollback_available"
    FAILED            = "failed"            # terminal
    ROLLED_BACK       = "rolled_back"       # terminal
```

### State Flow (with `COMMITTING` intermediate state)

The simplified mermaid diagram in `mri-migration-state-machine.md` omits the
`COMMITTING` intermediate state. The full path is:

```
PENDING → VALIDATING → VALIDATED → DRY_RUN → DRY_RUN_COMPLETE
       → READY_TO_COMMIT → COMMITTING → COMMITTED → ROLLBACK_AVAILABLE → ROLLED_BACK
```

`COMMITTING` is set at the start of `POST /batches/{id}/commit` and acts as a
pessimistic lock preventing concurrent commit attempts on the same batch.

### Test Impact

Tests that create a batch and assert `status == "created"` must be updated to assert
`status == "pending"`. `test_required_statuses_exist` in `test_migration_pipeline.py`
validates all 12 enum values.

---

## 6. Summary of Corrections

| Item                                 | Original spec       | Correct implementation                             |
|--------------------------------------|---------------------|----------------------------------------------------|
| `THRESHOLD_EXACT`                    | `>= 0.90`           | `>= 0.80` (max achievable score)                   |
| Grade D vs C evaluation order        | C before D          | D before C                                         |
| `_assert_transition` signature       | 2 args              | 3 args (`current_status, target_status, batch_id`) |
| `_compute_idempotency_key` signature | 4 args implied      | 5 positional args                                  |
| `MigrationBatchStatus` initial state | `"created"`         | `"pending"`                                        |
| Cross-building `score_pair` guard    | endpoint-level only | also in `score_pair()` at pair level               |
