from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.financial_core.domain.entities import (
    EntryDirection,
    PostGenesisJournalCommand,
    SchemeRef,
)
from services.financial_core.genesis import (
    compute_evidence_doc_hash,
    ensure_cutover_actor_user,
    post_import_based_genesis_cutover,
)
from services.financial_core.service import FinancialCoreService


@pytest.fixture
def scheme_ref():
    return SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())


@pytest.fixture
def fund_id():
    return uuid.uuid4()


@pytest.fixture
def mock_ledger():
    ledger = AsyncMock()
    ledger.count_journal_entries_for_fund = AsyncMock(return_value=0)
    ledger.get_current_accounting_period = AsyncMock(return_value=uuid.uuid4())
    ledger.get_gl_account = AsyncMock(side_effect=[uuid.uuid4(), uuid.uuid4()])
    ledger.set_fund_opening_balance = AsyncMock()

    async def _save_journal_entry(entry):
        entry.journal_entry_id = uuid.uuid4()
        return entry

    ledger.save_journal_entry = AsyncMock(side_effect=_save_journal_entry)
    ledger.post_journal_entry = AsyncMock(side_effect=lambda entry_id, scheme_ref: ledger.save_journal_entry.await_args_list[0].args[0])
    return ledger


@pytest.fixture
def mock_outbox():
    return AsyncMock()


def _service(mock_ledger, mock_outbox):
    return FinancialCoreService(ledger_repo=mock_ledger, outbox_port=mock_outbox)


@pytest.mark.asyncio
async def test_genesis_posts_balanced_entry(scheme_ref, fund_id, mock_ledger, mock_outbox):
    service = _service(mock_ledger, mock_outbox)
    cmd = PostGenesisJournalCommand(
        scheme_ref=scheme_ref,
        fund_id=fund_id,
        opening_balance_cents=1777811,
        as_at_date=date(2026, 4, 6),
        posted_by_user_id=uuid.uuid4(),
        posted_by_user_name="Cutover Admin",
        building_id="13195",
    )

    with patch("services.financial_core.service.create_audit_log", new=AsyncMock()):
        entry = await service.post_genesis_journal(cmd)

    assert entry.debit_total_cents == 1777811
    assert entry.credit_total_cents == 1777811
    assert [line.direction for line in entry.lines] == [EntryDirection.DEBIT, EntryDirection.CREDIT]


@pytest.mark.asyncio
async def test_genesis_idempotency_rejects_second_call(scheme_ref, fund_id, mock_outbox):
    ledger = AsyncMock()
    ledger.count_journal_entries_for_fund = AsyncMock(return_value=1)
    service = FinancialCoreService(ledger_repo=ledger, outbox_port=mock_outbox)
    cmd = PostGenesisJournalCommand(
        scheme_ref=scheme_ref,
        fund_id=fund_id,
        opening_balance_cents=100,
        as_at_date=date(2026, 4, 6),
    )

    with pytest.raises(ValueError, match="already has journal entries"):
        await service.post_genesis_journal(cmd)


@pytest.mark.asyncio
async def test_genesis_writes_outbox_row(scheme_ref, fund_id, mock_ledger, mock_outbox):
    service = _service(mock_ledger, mock_outbox)
    cmd = PostGenesisJournalCommand(
        scheme_ref=scheme_ref,
        fund_id=fund_id,
        opening_balance_cents=1777811,
        as_at_date=date(2026, 4, 6),
    )

    with patch("services.financial_core.service.create_audit_log", new=AsyncMock()):
        await service.post_genesis_journal(cmd)

    mock_outbox.publish.assert_awaited_once()
    assert mock_outbox.publish.await_args.kwargs["event_type"] == "fund.genesis_posted"


@pytest.mark.asyncio
async def test_genesis_writes_audit_log(scheme_ref, fund_id, mock_ledger, mock_outbox):
    service = _service(mock_ledger, mock_outbox)
    audit_log = AsyncMock()
    cmd = PostGenesisJournalCommand(
        scheme_ref=scheme_ref,
        fund_id=fund_id,
        opening_balance_cents=1777811,
        as_at_date=date(2026, 4, 6),
        posted_by_user_id=uuid.uuid4(),
        posted_by_user_name="Cutover Admin",
        building_id="13195",
    )

    with patch("services.financial_core.service.create_audit_log", new=audit_log):
        await service.post_genesis_journal(cmd)

    audit_log.assert_awaited_once()
    assert audit_log.await_args.kwargs["action"] == "genesis_journal_posted"


@pytest.mark.asyncio
async def test_genesis_handles_negative_opening_balance(scheme_ref, fund_id, mock_ledger, mock_outbox):
    service = _service(mock_ledger, mock_outbox)
    cmd = PostGenesisJournalCommand(
        scheme_ref=scheme_ref,
        fund_id=fund_id,
        opening_balance_cents=-2500,
        as_at_date=date(2026, 4, 6),
    )

    with patch("services.financial_core.service.create_audit_log", new=AsyncMock()):
        entry = await service.post_genesis_journal(cmd)

    assert [line.direction for line in entry.lines] == [EntryDirection.CREDIT, EntryDirection.DEBIT]
    assert all(line.amount_cents == 2500 for line in entry.lines)
    mock_ledger.set_fund_opening_balance.assert_awaited_once_with(fund_id, scheme_ref, -2500)


@pytest.mark.asyncio
async def test_genesis_evidence_doc_hash_binding(scheme_ref, fund_id, mock_ledger, mock_outbox):
    service = _service(mock_ledger, mock_outbox)
    cmd = PostGenesisJournalCommand(
        scheme_ref=scheme_ref,
        fund_id=fund_id,
        opening_balance_cents=1777811,
        as_at_date=date(2026, 4, 6),
        evidence_doc_hash="abc123",
    )

    with patch("services.financial_core.service.create_audit_log", new=AsyncMock()):
        entry = await service.post_genesis_journal(cmd)

    assert entry.metadata["evidence_doc_hash"] == "abc123"
    assert mock_outbox.publish.await_args.kwargs["payload"]["evidence_doc_hash"] == "abc123"


@pytest.mark.asyncio
async def test_full_cutover_against_test_data(scheme_ref):
    session = AsyncMock()
    admin_fund_id = uuid.uuid4()
    sinking_fund_id = uuid.uuid4()
    query_rows = [
        SimpleNamespace(fund_id=str(admin_fund_id), fund_type="admin_fund", fund_code="ADMIN", fund_name="Administrative Fund"),
        SimpleNamespace(fund_id=str(sinking_fund_id), fund_type="sinking_fund", fund_code="SINK", fund_name="Sinking Fund"),
    ]

    async def _execute(statement, params=None):
        sql = str(statement)
        if "FROM finance.funds" in sql:
            return SimpleNamespace(fetchall=lambda: query_rows)
        return SimpleNamespace()

    session.execute = AsyncMock(side_effect=_execute)
    mock_financial_core = AsyncMock()
    mock_financial_core.post_genesis_journal = AsyncMock(side_effect=[
        SimpleNamespace(journal_entry_id=uuid.uuid4()),
        SimpleNamespace(journal_entry_id=uuid.uuid4()),
    ])
    opening_balances = [
        {
            "fund_type": "admin",
            "opening_balance_cents": 1777811,
            "as_at_date": "2026-04-06",
            "account_number": "123456",
            "evidence_source": "bank_statement",
        },
        {
            "fund_type": "sinking",
            "opening_balance_cents": 15635148,
            "as_at_date": "2026-04-06",
            "account_number": "123456",
            "evidence_source": "bank_statement",
        },
    ]

    with (
        patch("services.financial_core.genesis.get_financial_core_service", return_value=mock_financial_core),
        patch("services.financial_core.genesis.set_tenant", new=AsyncMock()),
    ):
        entries, cutover_record = await post_import_based_genesis_cutover(
            session,
            scheme_ref=scheme_ref,
            opening_balances=opening_balances,
            posted_by_user_id=uuid.uuid4(),
            posted_by_user_name="Cutover Admin",
            building_id="13195",
            is_test_data=True,
        )

    assert len(entries) == 2
    assert mock_financial_core.post_genesis_journal.await_count == 2
    assert cutover_record["canonical_cutover_enabled"] is True
    assert "financial_integration_layer_v2" in cutover_record["enabled_feature_keys"]
    assert "financial_core.read_from_postgres" in cutover_record["legacy_feature_keys_removed"]
    assert cutover_record["funds"][0]["evidence_doc_hash"] == compute_evidence_doc_hash(opening_balances[0])


@pytest.mark.asyncio
async def test_scripted_cutover_actor_requires_allowlisted_name(scheme_ref):
    session = AsyncMock()

    with pytest.raises(ValueError, match="approved system actors"):
        await ensure_cutover_actor_user(
            session,
            scheme_ref=scheme_ref,
            building_id="13195",
            full_name="Unexpected Script Actor",
        )
