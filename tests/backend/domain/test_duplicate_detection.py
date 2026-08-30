"""
tests/backend/domain/test_duplicate_detection.py

Tests for domain/duplicate_detection.py — three-layer duplicate detection,
Levenshtein distance, content SHA-256, cross-building isolation.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId

from domain.duplicate_detection import (
    DuplicateCheckResult,
    _levenshtein,
    check_duplicate,
    content_sha256,
    normalise_ocr_text,
)

BUILDING_A = "13195"
BUILDING_B = "16244"
OID_EXISTING = ObjectId()
EXISTING_ID = str(OID_EXISTING)


def _make_db(find_one_result=None, candidates=None) -> MagicMock:
    mock_db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=candidates or [])
    mock_db.invoice_documents.find_one = AsyncMock(return_value=find_one_result)
    mock_db.invoice_documents.find.return_value = cursor
    return mock_db


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestNormaliseOcrText:
    def test_lowercases(self):
        assert normalise_ocr_text("HELLO WORLD") == "hello world"

    def test_collapses_whitespace(self):
        assert normalise_ocr_text("hello   world\n\t!") == "hello world !"

    def test_empty_string(self):
        assert normalise_ocr_text("") == ""

    def test_none_handled(self):
        assert normalise_ocr_text(None) == ""  # type: ignore[arg-type]


class TestContentSha256:
    def test_deterministic(self):
        assert content_sha256("Hello World") == content_sha256("Hello World")

    def test_normalised_equivalence(self):
        # Different whitespace → same hash after normalisation
        assert content_sha256("Invoice  Total  $1000") == content_sha256("invoice total $1000")

    def test_different_text_different_hash(self):
        assert content_sha256("Invoice A") != content_sha256("Invoice B")

    def test_returns_hex_string(self):
        h = content_sha256("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestLevenshtein:
    def test_identical_strings(self):
        assert _levenshtein("INV-001", "INV-001") == 0

    def test_single_substitution(self):
        assert _levenshtein("INV-001", "INV-002") == 1

    def test_single_deletion(self):
        assert _levenshtein("INV-001", "INV-01") == 1

    def test_two_edits(self):
        assert _levenshtein("INV-001", "INV-003") == 1  # single digit change
        assert _levenshtein("INV-001", "IN-001") == 1

    def test_completely_different(self):
        assert _levenshtein("ABC", "XYZ") == 3

    def test_empty_strings(self):
        assert _levenshtein("", "") == 0
        assert _levenshtein("abc", "") == 3
        assert _levenshtein("", "abc") == 3


# ── check_duplicate — Layer 1: exact match ────────────────────────────────────

class TestLayer1ExactMatch:
    @pytest.mark.asyncio
    async def test_exact_duplicate_detected(self):
        existing = {"_id": OID_EXISTING, "tenant_id": BUILDING_A, "invoice_number": "INV-001"}
        mock_db = _make_db(find_one_result=existing)
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        assert result.is_duplicate is True
        assert result.layer == 1
        assert result.existing_invoice_id == EXISTING_ID

    @pytest.mark.asyncio
    async def test_no_match_passes(self):
        mock_db = _make_db(find_one_result=None)
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-999", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        assert result.is_duplicate is False
        assert result.layer is None

    @pytest.mark.asyncio
    async def test_exclude_id_skips_self(self):
        existing = {"_id": OID_EXISTING, "tenant_id": BUILDING_A, "invoice_number": "INV-001"}
        mock_db = _make_db(find_one_result=existing)
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None,
            db=mock_db, exclude_id=EXISTING_ID,
        )
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_missing_abn_skips_layer1(self):
        mock_db = _make_db(find_one_result=None)
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn=None,
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        # No ABN → Layer 1 not run, no duplicate
        assert result.is_duplicate is False
        mock_db.invoice_documents.find_one.assert_not_called()


# ── check_duplicate — Layer 2: near-duplicate ─────────────────────────────────

class TestLayer2NearDuplicate:
    @pytest.mark.asyncio
    async def test_near_duplicate_detected(self):
        candidate = {
            "_id": OID_EXISTING,
            "tenant_id": BUILDING_A,
            "invoice_number": "INV-001",
            "total_cents": 100_00,
            "issue_date": "2026-04-01",
        }
        mock_db = _make_db(find_one_result=None, candidates=[candidate])
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-002",  # edit distance 1 from "INV-001"
            total_cents=100_00, issue_date="2026-04-01",
            content_hash=None, db=mock_db,
        )
        assert result.is_duplicate is True
        assert result.layer == 2

    @pytest.mark.asyncio
    async def test_edit_distance_3_not_near_dup(self):
        candidate = {
            "_id": OID_EXISTING,
            "tenant_id": BUILDING_A,
            "invoice_number": "INV-001",
            "total_cents": 100_00,
            "issue_date": "2026-04-01",
        }
        mock_db = _make_db(find_one_result=None, candidates=[candidate])
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-999",  # edit distance 3
            total_cents=100_00, issue_date="2026-04-01",
            content_hash=None, db=mock_db,
        )
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_layer2_uses_bounded_to_list(self):
        mock_db = _make_db(find_one_result=None, candidates=[])
        await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        # Verify .to_list(20) is called — bounded, not None
        cursor = mock_db.invoice_documents.find.return_value
        cursor.to_list.assert_called_once_with(20)


# ── check_duplicate — Layer 3: content SHA-256 ────────────────────────────────

class TestLayer3ContentHash:
    @pytest.mark.asyncio
    async def test_content_hash_match_detected(self):
        c_hash = content_sha256("Invoice text from Acme")
        existing = {"_id": OID_EXISTING, "tenant_id": BUILDING_A, "content_sha256": c_hash}
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        # vendor_abn=None skips L1 and L2; L3 is the only find_one call
        mock_db.invoice_documents.find_one = AsyncMock(return_value=existing)
        mock_db.invoice_documents.find.return_value = cursor

        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn=None,  # skip L1 and L2
            invoice_number=None, total_cents=100_00,
            issue_date=None, content_hash=c_hash, db=mock_db,
        )
        assert result.is_duplicate is True
        assert result.layer == 3
        assert result.existing_invoice_id == EXISTING_ID

    @pytest.mark.asyncio
    async def test_no_content_hash_skips_layer3(self):
        mock_db = _make_db(find_one_result=None)
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn=None,
            invoice_number=None, total_cents=100_00,
            issue_date=None, content_hash=None, db=mock_db,
        )
        assert result.is_duplicate is False


# ── Cross-building isolation ──────────────────────────────────────────────────

class TestCrossBuildingIsolation:
    @pytest.mark.asyncio
    async def test_duplicate_in_different_building_not_flagged(self):
        # Layer 1 returns a doc from BUILDING_B, not BUILDING_A
        existing = {"_id": OID_EXISTING, "tenant_id": BUILDING_B, "invoice_number": "INV-001"}
        # find_one returns None for BUILDING_A query (tenant_id scoped)
        mock_db = _make_db(find_one_result=None)
        result = await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_layer1_query_includes_tenant_id(self):
        mock_db = _make_db(find_one_result=None)
        await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        call_args = mock_db.invoice_documents.find_one.call_args[0][0]
        assert call_args.get("tenant_id") == BUILDING_A

    @pytest.mark.asyncio
    async def test_layer2_query_includes_tenant_id(self):
        mock_db = _make_db(find_one_result=None, candidates=[])
        await check_duplicate(
            tenant_id=BUILDING_A, vendor_abn="51824753556",
            invoice_number="INV-001", total_cents=100_00,
            issue_date="2026-04-01", content_hash=None, db=mock_db,
        )
        call_args = mock_db.invoice_documents.find.call_args[0][0]
        assert call_args.get("tenant_id") == BUILDING_A
