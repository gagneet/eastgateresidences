"""Unit tests for services/onboarding/gap_tolerant_financial_import.py.

parse_rows() is pure (no DB) and tested directly. import_gap_tolerant_financial_data() needs a
Mongo-like db — a minimal fake (not AsyncMock) is used since it does read-your-writes upserts
(find_one then insert/update) that would be verbose to express with mocked return values.
"""
from __future__ import annotations

import json

import pytest

from services.onboarding.gap_tolerant_financial_import import (
    FinancialImportError,
    import_gap_tolerant_financial_data,
    parse_rows,
)

BUILDING_ID = "16244"


def _apply_dotted_set(doc: dict, set_fields: dict) -> None:
    """Mimic real MongoDB's dot-notation $set semantics (e.g. "admin_fund.levy_income" sets a
    NESTED field, not a literal flat key containing a dot) — the production code
    (import_gap_tolerant_financial_data) relies on real Mongo doing this, so the fake must too,
    or tests would pass against fake-only behaviour that doesn't reflect production."""
    for dotted_key, value in set_fields.items():
        parts = dotted_key.split(".")
        target = doc
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    async def find_one(self, query: dict):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                _apply_dotted_set(doc, update.get("$set", {}))
                return
        if upsert:
            new_doc = dict(query)
            _apply_dotted_set(new_doc, update.get("$set", {}))
            new_doc.update(update.get("$setOnInsert", {}))
            self._docs.append(new_doc)

    async def insert_one(self, doc: dict):
        self._docs.append(dict(doc))

    def find(self, query: dict):
        matches = [doc for doc in self._docs if all(doc.get(k) == v for k, v in query.items())]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def to_list(self, _n):
                async def _inner():
                    return list(self._items)
                return _inner()

        return _Cursor(matches)


class _FakeMongoDb:
    def __init__(self):
        self.annual_levies = _FakeCollection()
        self.levy_categories = _FakeCollection()


class TestParseRowsCsv:
    def test_basic_csv(self):
        content = b"year,fund_type,proposed_amount,closing_balance\n2024,admin,340870.02,50000.00\n"
        rows = parse_rows(content, "budget.csv")
        assert len(rows) == 1
        assert rows[0]["year"] == "2024"
        assert rows[0]["fund_type"] == "admin"

    def test_missing_required_column_raises(self):
        content = b"year,proposed_amount\n2024,1000\n"  # no fund_type
        with pytest.raises(FinancialImportError, match="fund_type"):
            parse_rows(content, "budget.csv")

    def test_no_header_row_raises(self):
        with pytest.raises(FinancialImportError):
            parse_rows(b"", "budget.csv")

    def test_no_data_rows_raises(self):
        content = b"year,fund_type\n"
        with pytest.raises(FinancialImportError, match="no data rows"):
            parse_rows(content, "budget.csv")

    def test_case_insensitive_headers(self):
        content = b"Year,Fund_Type,Proposed_Amount\n2024,ADMIN,1000\n"
        rows = parse_rows(content, "budget.csv")
        assert rows[0]["year"] == "2024"
        assert rows[0]["fund_type"] == "ADMIN"


class TestParseRowsJson:
    def test_basic_json(self):
        content = json.dumps([{"year": 2024, "fund_type": "admin", "proposed_amount": 1000}]).encode()
        rows = parse_rows(content, "budget.json")
        assert len(rows) == 1
        assert rows[0]["fund_type"] == "admin"

    def test_json_not_a_list_raises(self):
        content = json.dumps({"year": 2024}).encode()
        with pytest.raises(FinancialImportError, match="top-level array"):
            parse_rows(content, "budget.json")

    def test_malformed_json_raises(self):
        with pytest.raises(FinancialImportError, match="Could not parse JSON"):
            parse_rows(b"{not valid json", "budget.json")


class TestImportGapTolerantFinancialData:
    @pytest.mark.asyncio
    async def test_proposed_amount_and_closing_balance_upserted(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount,closing_balance\n2024,admin,340870.02,50000.00\n"
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        assert result["fund_rows_written"] == 1
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert doc["admin_fund"]["levy_income"] == 340870.02
        assert doc["admin_fund"]["closing_balance"] == 50000.00

    @pytest.mark.asyncio
    async def test_category_row_upserted(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,category_name,category_actual_amount\n2024,sinking,Lift Replacement,44800\n"
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        assert result["category_rows_written"] == 1
        doc = await db.levy_categories.find_one(
            {"building_id": BUILDING_ID, "year": "2024", "name": "Lift Replacement", "fund_type": "sinking"}
        )
        assert doc["actual_amount"] == 44800.0
        assert doc["status"] == "actual"

    @pytest.mark.asyncio
    async def test_category_proposed_amount_is_stored_as_budgeted_amount(self):
        db = _FakeMongoDb()
        content = (
            b"year,fund_type,category_name,category_proposed_amount,category_actual_amount\n"
            b"2024,admin,Insurance,12000.50,8450.25\n"
        )
        await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        doc = await db.levy_categories.find_one(
            {"building_id": BUILDING_ID, "year": "2024", "name": "Insurance", "fund_type": "admin"}
        )
        assert doc["budgeted_amount"] == 12000.50
        assert doc["actual_amount"] == 8450.25
        assert doc["status"] == "actual"

    @pytest.mark.asyncio
    async def test_category_without_actual_amount_defaults_to_zero_and_proposed_status(self):
        """Gap-tolerant: a category name with no actual amount yet (proposed at AGM, not spent
        yet) is still recorded, matching the existing importer's own default-to-0.0 convention."""
        db = _FakeMongoDb()
        content = b"year,fund_type,category_name\n2024,admin,Insurance\n"
        await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        doc = await db.levy_categories.find_one(
            {"building_id": BUILDING_ID, "year": "2024", "name": "Insurance", "fund_type": "admin"}
        )
        assert doc["budgeted_amount"] == 0.0
        assert doc["actual_amount"] == 0.0
        assert doc["status"] == "proposed"

    @pytest.mark.asyncio
    async def test_reupload_missing_amount_does_not_overwrite_existing_real_value(self):
        """A re-upload whose row for an existing category omits the amount column must never
        blank out a previously reported actual_amount/status. The 0.0/'proposed' placeholder is
        valid only for inserting a brand-new category."""
        db = _FakeMongoDb()
        content1 = b"year,fund_type,category_name,category_actual_amount\n2024,admin,Insurance,8450.00\n"
        content2 = b"year,fund_type,category_name\n2024,admin,Insurance\n"  # no amount column
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content1, filename="a.csv", uploaded_by="u1")
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content2, filename="b.csv", uploaded_by="u1")

        doc = await db.levy_categories.find_one(
            {"building_id": BUILDING_ID, "year": "2024", "name": "Insurance", "fund_type": "admin"}
        )
        assert doc["actual_amount"] == 8450.00  # NOT silently reset to 0.0
        assert doc["status"] == "actual"  # NOT silently reset to "proposed"

    @pytest.mark.asyncio
    async def test_combined_fund_and_category_in_one_row(self):
        db = _FakeMongoDb()
        content = (
            b"year,fund_type,proposed_amount,closing_balance,category_name,category_actual_amount\n"
            b"2024,admin,340870.02,50000.00,Insurance,8450.00\n"
        )
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        assert result["fund_rows_written"] == 1
        assert result["category_rows_written"] == 1

    @pytest.mark.asyncio
    async def test_invalid_fund_type_row_skipped_with_warning(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\n2024,not_a_fund,1000\n"
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        assert result["fund_rows_written"] == 0
        assert any("fund_type" in w for w in result["row_warnings"])

    @pytest.mark.asyncio
    async def test_unparseable_year_row_skipped_with_warning(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\nnot-a-year,admin,1000\n"
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        assert result["fund_rows_written"] == 0
        assert any("year" in w for w in result["row_warnings"])

    @pytest.mark.asyncio
    async def test_reupload_for_same_year_upserts_not_duplicates(self):
        db = _FakeMongoDb()
        content1 = b"year,fund_type,proposed_amount\n2024,admin,1000\n"
        content2 = b"year,fund_type,proposed_amount\n2024,admin,2000\n"
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content1, filename="a.csv", uploaded_by="u1")
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content2, filename="b.csv", uploaded_by="u1")

        assert len(db.annual_levies._docs) == 1
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert doc["admin_fund"]["levy_income"] == 2000

    @pytest.mark.asyncio
    async def test_category_reupload_upserts_not_duplicates(self):
        """Category-level writes use an atomic update_one(upsert=True), not a separate
        find_one + insert_one/update_one — a corrected re-upload for the same
        year/category/fund_type must update the existing row, never create a second one."""
        db = _FakeMongoDb()
        content1 = b"year,fund_type,category_name,category_actual_amount\n2024,admin,Insurance,8000\n"
        content2 = b"year,fund_type,category_name,category_actual_amount\n2024,admin,Insurance,8450\n"
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content1, filename="a.csv", uploaded_by="u1")
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content2, filename="b.csv", uploaded_by="u1")

        assert len(db.levy_categories._docs) == 1
        doc = await db.levy_categories.find_one(
            {"building_id": BUILDING_ID, "year": "2024", "name": "Insurance", "fund_type": "admin"}
        )
        assert doc["actual_amount"] == 8450.0
        assert doc["id"]  # $setOnInsert must have fired exactly once, not been skipped entirely

    @pytest.mark.asyncio
    async def test_coverage_summary_reflects_gaps(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\n2024,admin,1000\n"  # no sinking, no closing balance
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        coverage = result["coverage"]
        assert len(coverage) == 1
        entry = coverage[0]
        assert entry["year"] == 2024
        assert entry["proposed_admin_present"] is True
        assert entry["proposed_sinking_present"] is False
        assert entry["closing_balance_admin_present"] is False
        assert entry["category_count"] == 0

    @pytest.mark.asyncio
    async def test_actual_income_and_actual_spent_stored_as_cents(self):
        """New control fields are stored as integer cents, unlike the pre-existing
        levy_income/closing_balance float-dollar fields."""
        db = _FakeMongoDb()
        content = (
            b"year,fund_type,opening_balance,actual_income,actual_spent\n"
            b"2024,admin,23374.75,248970.54,216901.65\n"
        )
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert doc["admin_fund"]["opening_balance_cents"] == 2337475
        assert doc["admin_fund"]["actual_income_cents"] == 24897054
        assert doc["admin_fund"]["actual_spent_cents"] == 21690165

    @pytest.mark.asyncio
    async def test_missing_new_control_fields_is_gap_tolerant(self):
        """A row supplying only the pre-existing fields must not raise or warn about the new
        ones — every new field is optional, matching this importer's entire design."""
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\n2024,admin,1000\n"
        result = await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        assert result["row_warnings"] == []
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert "opening_balance_cents" not in doc["admin_fund"]
        assert "actual_income_cents" not in doc["admin_fund"]
        assert "actual_spent_cents" not in doc["admin_fund"]

    @pytest.mark.asyncio
    async def test_control_dates_stored_at_year_level_not_nested_per_fund(self):
        db = _FakeMongoDb()
        content = (
            b"year,fund_type,proposed_amount,control_balance_as_of_date,expense_ledger_cutoff_date\n"
            b"2024,admin,1000,2024-12-31,2024-04-08\n"
        )
        await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert doc["control_balance_as_of_date"] == "2024-12-31"
        assert doc["expense_ledger_cutoff_date"] == "2024-04-08"
        assert "control_balance_as_of_date" not in doc["admin_fund"]

    @pytest.mark.asyncio
    async def test_malformed_control_date_warns_not_raises(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount,control_balance_as_of_date\n2024,admin,1000,not-a-date\n"
        result = await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        assert any("control_balance_as_of_date" in w for w in result["row_warnings"])
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert "control_balance_as_of_date" not in doc

    @pytest.mark.asyncio
    async def test_coverage_summary_reflects_new_control_field_presence(self):
        db = _FakeMongoDb()
        content = (
            b"year,fund_type,actual_income,control_balance_as_of_date\n"
            b"2024,admin,248970.54,2024-12-31\n"
        )
        result = await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        entry = result["coverage"][0]
        assert entry["actual_income_admin_present"] is True
        assert entry["actual_income_sinking_present"] is False
        assert entry["actual_spent_admin_present"] is False
        assert entry["control_balance_as_of_date"] == "2024-12-31"
        assert entry["expense_ledger_cutoff_date"] is None

    @pytest.mark.asyncio
    async def test_json_upload_end_to_end(self):
        db = _FakeMongoDb()
        content = json.dumps([
            {"year": 2024, "fund_type": "sinking", "proposed_amount": 99504.90, "closing_balance": 12000.0},
        ]).encode()
        result = await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.json", uploaded_by="user-1",
        )
        assert result["fund_rows_written"] == 1
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert doc["sinking_fund"]["levy_income"] == 99504.90

    @pytest.mark.asyncio
    async def test_is_test_data_flag_stamped_on_written_docs(self):
        """Mandatory repo convention: any record created by a test/perf script must carry
        is_test_data=True so it can be excluded from real reconstructions and swept in cleanup."""
        db = _FakeMongoDb()
        content = (
            b"year,fund_type,proposed_amount,closing_balance,category_name,category_actual_amount\n"
            b"2024,admin,340870.02,50000.00,Insurance,8450.00\n"
        )
        await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
            is_test_data=True,
        )
        levy_doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        cat_doc = await db.levy_categories.find_one({"building_id": BUILDING_ID, "year": "2024", "name": "Insurance", "fund_type": "admin"})
        assert levy_doc["is_test_data"] is True
        assert cat_doc["is_test_data"] is True

    @pytest.mark.asyncio
    async def test_is_test_data_defaults_false(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\n2024,admin,1000\n"
        await import_gap_tolerant_financial_data(
            db, building_id=BUILDING_ID, content=content, filename="budget.csv", uploaded_by="user-1",
        )
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2024"})
        assert doc["is_test_data"] is False

    @pytest.mark.asyncio
    async def test_building_isolation(self):
        """A row for building A must never be visible/counted for building B."""
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\n2024,admin,1000\n"
        await import_gap_tolerant_financial_data(db, building_id="AAAA", content=content, filename="a.csv", uploaded_by="u1")

        result_b = await import_gap_tolerant_financial_data(
            db, building_id="BBBB", content=content, filename="b.csv", uploaded_by="u1",
        )
        doc_a = await db.annual_levies.find_one({"building_id": "AAAA", "year": "2024"})
        doc_b = await db.annual_levies.find_one({"building_id": "BBBB", "year": "2024"})
        assert doc_a is not None and doc_b is not None
        assert doc_a is not doc_b
        assert result_b["coverage"][0]["proposed_admin_present"] is True


class TestLevyFrequency:
    @pytest.mark.asyncio
    async def test_recognised_frequency_stored_at_year_level(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount,levy_frequency\n2021,admin,1000,half_yearly\n"
        result = await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        assert result["row_warnings"] == []
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2021"})
        assert doc["levy_frequency"] == "half_yearly"
        assert "levy_frequency" not in doc["admin_fund"]  # year-level, not nested per fund

    @pytest.mark.asyncio
    async def test_unrecognised_frequency_defaults_to_quarterly_with_warning(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount,levy_frequency\n2021,admin,1000,fortnightly\n"
        result = await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        assert any("levy_frequency" in w for w in result["row_warnings"])
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2021"})
        assert doc["levy_frequency"] == "quarterly"

    @pytest.mark.asyncio
    async def test_missing_levy_frequency_is_gap_tolerant(self):
        db = _FakeMongoDb()
        content = b"year,fund_type,proposed_amount\n2021,admin,1000\n"
        result = await import_gap_tolerant_financial_data(db, building_id=BUILDING_ID, content=content, filename="a.csv", uploaded_by="u1")
        assert result["row_warnings"] == []
        doc = await db.annual_levies.find_one({"building_id": BUILDING_ID, "year": "2021"})
        assert "levy_frequency" not in doc
