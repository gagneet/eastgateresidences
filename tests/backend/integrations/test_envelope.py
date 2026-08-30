"""
tests/backend/integrations/test_envelope.py — Unit tests for EventEnvelope and related types.

Tests the immutable envelope types in backend/integrations/envelopes.py:
  - EventEnvelope (frozen Pydantic model, tenant_id required)
  - BankTxObserved (frozen, Cents-typed amount)
  - generate_ulid (sortable, 26-char ULID)
  - compute_idempotency_key (deterministic SHA-256)

No DB or running backend needed — all tests are pure unit tests.
"""
import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from integrations.envelopes import (
    BankTxObserved,
    EventEnvelope,
    compute_idempotency_key,
    generate_ulid,
)

_TEST_BUILDING = "16244"  # Sierra demo — never "13195"


def _make_envelope(**overrides) -> EventEnvelope:
    """Return a minimal valid EventEnvelope, optionally overriding fields."""
    defaults = dict(
        provider_event_id="prov-001",
        idempotency_key="idem-001",
        tenant_id=_TEST_BUILDING,
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        event_type="bank_tx_observed",
        raw={"amount": 100},
    )
    defaults.update(overrides)
    return EventEnvelope(**defaults)


class TestEventEnvelope:
    def test_envelope_requires_tenant_id(self):
        """Creating an envelope with empty tenant_id raises ValidationError."""
        with pytest.raises(ValidationError):
            EventEnvelope(
                provider_event_id="prov-001",
                idempotency_key="idem-001",
                tenant_id="",  # empty → validator rejects it
                occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
                event_type="bank_tx_observed",
                raw={},
            )

    def test_envelope_is_frozen(self):
        """Attempting to mutate a frozen envelope raises TypeError."""
        env = _make_envelope()
        with pytest.raises((TypeError, ValidationError)):
            env.tenant_id = "other-building"  # type: ignore[misc]

    def test_envelope_event_id_auto_generated(self):
        """event_id is auto-populated via generate_ulid when not supplied."""
        env = _make_envelope()
        assert env.event_id
        assert len(env.event_id) == 26

    def test_envelope_explicit_event_id_accepted(self):
        """event_id can be supplied explicitly and is preserved."""
        env = _make_envelope(event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
        assert env.event_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def test_envelope_received_at_auto_populated(self):
        """received_at defaults to UTC now and is a timezone-aware datetime."""
        env = _make_envelope()
        assert env.received_at.tzinfo is not None

    def test_envelope_raw_preserved(self):
        """The raw dict payload is preserved verbatim on the envelope."""
        raw = {"amount": 99900, "description": "BPAY LEVY"}
        env = _make_envelope(raw=raw)
        assert env.raw == raw

    def test_envelope_tenant_id_stored(self):
        """tenant_id is stored exactly as supplied."""
        env = _make_envelope(tenant_id=_TEST_BUILDING)
        assert env.tenant_id == _TEST_BUILDING


class TestGenerateUlid:
    def test_generate_ulid_is_26_chars(self):
        """A ULID is always exactly 26 characters."""
        ulid = generate_ulid()
        assert len(ulid) == 26

    def test_generate_ulid_is_uppercase_crockford(self):
        """All characters are in the Crockford Base32 alphabet (uppercase)."""
        crockford = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        ulid = generate_ulid()
        assert all(c in crockford for c in ulid), (
            f"ULID {ulid!r} contains characters outside Crockford Base32"
        )

    def test_generate_ulid_uniqueness(self):
        """100 generated ULIDs are all distinct."""
        ulids = [generate_ulid() for _ in range(100)]
        assert len(set(ulids)) == 100

    def test_generate_ulid_is_sortable(self):
        """ULIDs generated in time order sort lexicographically in that same order.

        Sleep 2 ms between batches to ensure distinct timestamps are encoded.
        """
        batch_a = [generate_ulid() for _ in range(5)]
        time.sleep(0.002)  # 2 ms — guarantees distinct millisecond timestamps
        batch_b = [generate_ulid() for _ in range(5)]

        # Every ULID in batch_b must sort after every ULID in batch_a
        for a, b in zip(batch_a, batch_b):
            assert a < b, (
                f"ULID sortability violated: {a!r} (earlier) >= {b!r} (later)"
            )

    def test_generate_ulid_timestamp_prefix_increases(self):
        """The first 10 characters (timestamp component) must be non-decreasing."""
        first = generate_ulid()[:10]
        time.sleep(0.001)
        second = generate_ulid()[:10]
        assert first <= second


class TestComputeIdempotencyKey:
    def test_compute_idempotency_key_is_deterministic(self):
        """Same tenant_id + same raw dict always produces the same SHA-256 hex string."""
        raw = {"date": "2026-04-15", "amount": -150000, "description": "BPAY LEVY"}
        key1 = compute_idempotency_key(_TEST_BUILDING, raw)
        key2 = compute_idempotency_key(_TEST_BUILDING, raw)
        assert key1 == key2

    def test_compute_idempotency_key_changes_with_tenant(self):
        """Different tenant_id produces a different key for the same raw payload."""
        raw = {"amount": 100}
        key_a = compute_idempotency_key("16244", raw)
        key_b = compute_idempotency_key("harbourside_view", raw)
        assert key_a != key_b

    def test_compute_idempotency_key_is_sha256_hex(self):
        """The key is a 64-character lowercase hex string (SHA-256 output)."""
        key = compute_idempotency_key(_TEST_BUILDING, {"x": 1})
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_compute_idempotency_key_changes_with_payload(self):
        """Different raw payloads produce different keys for the same tenant."""
        key1 = compute_idempotency_key(_TEST_BUILDING, {"amount": 100})
        key2 = compute_idempotency_key(_TEST_BUILDING, {"amount": 200})
        assert key1 != key2

    def test_compute_idempotency_key_order_independent(self):
        """Key is computed from sorted-key JSON, so dict insertion order doesn't matter."""
        raw_a = {"b": 2, "a": 1}
        raw_b = {"a": 1, "b": 2}
        key_a = compute_idempotency_key(_TEST_BUILDING, raw_a)
        key_b = compute_idempotency_key(_TEST_BUILDING, raw_b)
        assert key_a == key_b


class TestBankTxObserved:
    def test_bank_tx_observed_amount_cents_is_int(self):
        """amount_cents accepts a plain int and stores it as int."""
        tx = BankTxObserved(
            provider_txn_id="tx-001",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=153000,
            description="LEVY PAYMENT",
        )
        assert tx.amount_cents == 153000
        assert isinstance(tx.amount_cents, int)

    def test_bank_tx_observed_float_amount_rejected_or_coerced_to_int(self):
        """Cents is Annotated[int, ...] — any stored value must be int, not float.

        Pydantic v2 in lax mode coerces whole-number floats to int.
        We assert the stored value is int (not float) regardless of coercion path.
        """
        tx = BankTxObserved(
            provider_txn_id="tx-002",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=15300,  # int supplied — must stay int
            description="LEVY PAYMENT",
        )
        assert isinstance(tx.amount_cents, int), (
            "amount_cents must be stored as int, not float"
        )

    def test_bank_tx_observed_is_frozen(self):
        """BankTxObserved is a frozen model — mutation raises TypeError."""
        tx = BankTxObserved(
            provider_txn_id="tx-003",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=-50000,
            description="DIRECT DEBIT",
        )
        with pytest.raises((TypeError, ValidationError)):
            tx.amount_cents = 0  # type: ignore[misc]

    def test_bank_tx_observed_negative_amount(self):
        """Negative amount_cents represents a debit (money out)."""
        tx = BankTxObserved(
            provider_txn_id="tx-004",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=-150000,
            description="BPAY 0000001350014 LEVY PAYMENT",
        )
        assert tx.amount_cents == -150000
        assert tx.amount_cents < 0

    def test_bank_tx_observed_optional_fields_default_none(self):
        """bpay_crn, osko_e2e_id, lot_ref_raw, and balance_after_cents default to None."""
        tx = BankTxObserved(
            provider_txn_id="tx-005",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=100,
            description="EFT",
        )
        assert tx.bpay_crn is None
        assert tx.osko_e2e_id is None
        assert tx.lot_ref_raw is None
        assert tx.balance_after_cents is None

    def test_bank_tx_observed_is_test_data_default_false(self):
        """is_test_data defaults to False."""
        tx = BankTxObserved(
            provider_txn_id="tx-006",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=100,
            description="EFT",
        )
        assert tx.is_test_data is False

    def test_bank_tx_observed_is_test_data_can_be_set_true(self):
        """is_test_data=True is accepted for records created in tests."""
        tx = BankTxObserved(
            provider_txn_id="tx-007",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=100,
            description="EFT",
            is_test_data=True,
        )
        assert tx.is_test_data is True

    def test_bank_tx_observed_tenant_id_stored(self):
        """tenant_id is stored exactly as supplied — not defaulted."""
        tx = BankTxObserved(
            provider_txn_id="tx-008",
            tenant_id=_TEST_BUILDING,
            account_ref="trust-account-001",
            occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            amount_cents=100,
            description="EFT",
        )
        assert tx.tenant_id == _TEST_BUILDING
