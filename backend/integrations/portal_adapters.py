# @featuretrace:strata-web-portal-finance-ingest — Pluggable portal/current-balance intake.
# Layer: service
# Data flow: POST /settings/strata-web-portal/sync -> ProviderRegistry.get_portal_adapter(building_id)
#            -> PortalAdapter.build_snapshot() -> canonical PortalBalanceSnapshot
#            -> staging_strata_web_snapshots (building-scoped) -> reconciliation (never a journal).
# Related: backend/integrations/registry.py (selection), backend/routers/strata_web_portal.py (sync trigger),
#          backend/scripts/ingest/strata_web_portal_ingest.py (CLI, delegates here),
#          backend/services/portal_ledger_reconciliation_service.py (snapshot consumer).
"""PortalAdapter seam (GAP-ONBOARD-003).

The onboarding financial pipeline's portal/current-balance INTAKE was Civium-shaped: the
in-repo staging step (`strata_web_portal_ingest.build_snapshot`) read one provider's raw Mongo
collections (`bank_accounts`/`building_summaries`/`strata_owners`) with that provider's field
names, so a non-Civium building had no path to stage `portal_balance_snapshot` evidence through
the generic pipeline.

This module introduces a pluggable `PortalAdapter` protocol (mirroring
`backend/integrations/registry.py`'s provider-registry pattern) whose only job is to produce the
**canonical** `PortalBalanceSnapshot` for a building from that building's portal source. Everything
downstream of the snapshot (staging upsert, reconciliation) is already provider-agnostic, so a
building can now onboard through any adapter that emits this one typed shape.

A portal snapshot is reconciliation/verification EVIDENCE (per-lot current net + fund balances),
never a journal source — see the Financial Evidence Gateway rules. This seam only generalises how
that evidence is *produced*, not what it *is* or how it is consumed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# Canonical adapter names (the keys used in db.settings.integration_provider_preference.portal).
CIVIUM_PORTAL = "strata_web_portal_scrape"   # keep the legacy `source` value for back-compat
CSV_PORTAL = "csv_portal_export"


def cents(value: Any) -> Optional[int]:
    """Convert a dollar-denominated portal value to integer cents (half-up), or None.

    External-source amounts are converted to integer cents at this ingestion boundary — once —
    exactly as the legacy ingest did (Ledger Money Precision rule).
    """
    if value is None or value == "":
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def date_part(value: Any) -> Optional[str]:
    """Return the ISO date (YYYY-MM-DD) part of a date/datetime/string, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value)
    return text[:10] if len(text) >= 10 else None


def year_candidates(year: str) -> list[str]:
    """Both the plain ('2026') and hyphenated ('2026-2027') forms of a financial year, so a
    provider stored under either convention still matches."""
    y = str(year).strip()
    if "-" in y:
        return [y, y.split("-", 1)[0]]
    try:
        return [y, f"{int(y)}-{int(y) + 1}"]
    except ValueError:
        return [y]


class PortalUnitBalance(BaseModel):
    """One lot's current portal balance. `balance_cents` is the portal's outstanding net for the
    lot (verification evidence, not a ledger figure)."""
    lot_number: str
    # The ADDRESSABLE unit number ("TH079"), as distinct from `lot_number`, which is the
    # plan lot number ("79"). Both are needed and they are NOT interchangeable — CLAUDE.md
    # footgun #16. Anything a person, a levy notice or a matcher refers to is the UNIT.
    #
    # Added 2026-08-28. Without it the snapshot dropped the unit number entirely, so
    # balance-delta inference emitted lot numbers as the unit reference and the matching
    # engine's L4 layer compared "3" against candidates whose unit_number is "UA003" —
    # 32 of 39 real payments scored 0.0 and landed as `unidentified`.
    #
    # Optional because snapshots staged before this field existed do not carry it;
    # consumers must fall back rather than assume. Additive only — the frozen field
    # contract below is about not RENAMING or REMOVING fields.
    unit_number: Optional[str] = None
    balance_cents: int = 0
    status: Optional[str] = None
    owner_name: Optional[str] = None


class PortalBalanceSnapshot(BaseModel):
    """The canonical `staging_strata_web_snapshots` document — the single typed evidence contract
    EVERY `PortalAdapter` must emit, so downstream staging + reconciliation stay provider-agnostic.
    Field names are frozen: existing staged snapshots and consumers
    (`portal_ledger_reconciliation_service`, `finance_reconciliation`) read these exact keys.
    """
    building_id: str
    snapshot_date: str
    financial_year: str
    source: str
    raw_admin_fund_balance_cents: int = 0
    raw_sinking_fund_balance_cents: int = 0
    raw_arrears_total_cents: int = 0
    raw_credit_total_cents: int = 0
    raw_collection_rate: Optional[float] = None
    per_unit_balances: list[PortalUnitBalance] = Field(default_factory=list)
    is_test_data: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_staging_document(self) -> dict[str, Any]:
        """The exact dict shape written to `staging_strata_web_snapshots` (identical to the legacy
        ingest's output, so downstream consumers and the upsert key are unchanged)."""
        return self.model_dump(mode="python")


@runtime_checkable
class PortalAdapter(Protocol):
    """Produces the canonical PortalBalanceSnapshot for a building from a provider's portal source.

    Implementations gather provider-specific raw data and MUST return a `PortalBalanceSnapshot`
    (never a bespoke dict) so every building's portal evidence is identically typed. The snapshot is
    reconciliation evidence only — an adapter never posts to a journal.
    """

    name: str

    async def build_snapshot(
        self,
        building_id: str,
        financial_year: str,
        snapshot_date: Optional[str] = None,
    ) -> PortalBalanceSnapshot:
        ...


# `db` is imported at module scope so tests can patch `integrations.portal_adapters.db`. The Civium
# adapter reads the raw scraped collections via `db._db` exactly as the legacy ingest did.
try:  # pragma: no cover - import guard mirrors the rest of the codebase
    from database import db
except Exception:  # pragma: no cover
    db = None  # type: ignore


class CiviumPortalAdapter:
    """First concrete adapter — the existing Civium (my.civium.com.au) intake, unchanged.

    The actual screen-scrape runs out-of-repo and populates
    `bank_accounts`/`building_summaries`/`strata_owners`; this adapter aggregates those into the
    canonical snapshot. Behaviour for East Gate (13195) is identical to the pre-seam
    `strata_web_portal_ingest.build_snapshot` — same `source` value, same fields, same math.
    """

    name = CIVIUM_PORTAL

    async def build_snapshot(
        self,
        building_id: str,
        financial_year: str,
        snapshot_date: Optional[str] = None,
    ) -> PortalBalanceSnapshot:
        candidates = year_candidates(financial_year)
        latest_bank = await db._db.bank_accounts.find_one(
            {"building_id": building_id}, sort=[("updated_at", -1)]
        )
        summary = await db._db.building_summaries.find_one(
            {"building_id": building_id, "financial_year": {"$in": candidates}},
            sort=[("updated_at", -1), ("generated_at", -1), ("computed_at", -1)],
        )
        if not summary:
            summary = await db._db.building_summaries.find_one(
                {"building_id": building_id}, sort=[("updated_at", -1)]
            )
        owner_docs = await db._db.strata_owners.find(
            {"building_id": building_id}
        ).sort("lot", 1).to_list(None)

        inferred_date = (
            snapshot_date
            or date_part((summary or {}).get("updated_at"))
            or date_part((latest_bank or {}).get("updated_at"))
            or date.today().isoformat()
        )
        per_unit = [
            PortalUnitBalance(
                lot_number=str(
                    owner.get("lot") or owner.get("lot_number")
                    or owner.get("unit") or owner.get("unit_number") or ""
                ),
                # strata_owners carries BOTH: `lot` (71) and `unit_number` ("TH071").
                # Keep them apart — see PortalUnitBalance.unit_number.
                unit_number=(str(owner["unit_number"]) if owner.get("unit_number") else None),
                balance_cents=cents(owner.get("balance")) or 0,
                status=owner.get("status"),
                owner_name=owner.get("owner_name") or owner.get("owner"),
            )
            for owner in owner_docs
        ]

        admin_balance = (summary or {}).get("admin_fund_balance_cents")
        sinking_balance = (summary or {}).get("sinking_fund_balance_cents")
        if not admin_balance and latest_bank:
            admin_balance = cents(latest_bank.get("admin_balance"))
        if not sinking_balance and latest_bank:
            sinking_balance = cents(latest_bank.get("sinking_balance"))

        return PortalBalanceSnapshot(
            building_id=building_id,
            snapshot_date=inferred_date,
            financial_year=str(financial_year),
            source=self.name,
            raw_admin_fund_balance_cents=int(admin_balance or 0),
            raw_sinking_fund_balance_cents=int(sinking_balance or 0),
            raw_arrears_total_cents=int(
                (summary or {}).get("arrears_cents")
                or cents((summary or {}).get("arrears_total")) or 0
            ),
            raw_credit_total_cents=int(cents((summary or {}).get("credit_total")) or 0),
            raw_collection_rate=(summary or {}).get("collection_rate"),
            per_unit_balances=per_unit,
        )


class CsvPortalAdapter:
    """Second concrete adapter — proof of genericity (GAP-ONBOARD-003 item 4).

    Produces the SAME canonical snapshot from a provider-neutral, in-memory export (a list of
    per-lot rows + fund totals) instead of Civium's Mongo collections. A real per-provider CSV/API
    integration would parse its export into these rows; the mock takes them directly so any building
    with a plain balance export can stage identical typed evidence with zero Civium coupling. Used
    by the cross-provider genericity test and as the CI/new-building default source.
    """

    name = CSV_PORTAL

    def __init__(
        self,
        *,
        rows: Optional[list[dict[str, Any]]] = None,
        admin_fund_balance: Any = None,
        sinking_fund_balance: Any = None,
        arrears_total: Any = None,
        credit_total: Any = None,
        collection_rate: Optional[float] = None,
        is_test_data: bool = False,
    ) -> None:
        self._rows = rows or []
        self._admin = admin_fund_balance
        self._sinking = sinking_fund_balance
        self._arrears = arrears_total
        self._credit = credit_total
        self._collection_rate = collection_rate
        self._is_test_data = is_test_data

    async def build_snapshot(
        self,
        building_id: str,
        financial_year: str,
        snapshot_date: Optional[str] = None,
    ) -> PortalBalanceSnapshot:
        per_unit = [
            PortalUnitBalance(
                lot_number=str(
                    row.get("lot_number") or row.get("lot")
                    or row.get("unit_number") or row.get("unit") or ""
                ),
                # Accept either pre-cented (`balance_cents`) or dollar (`balance`) inputs, converting
                # dollars to cents at this boundary — same rule as every external-source ingestion.
                balance_cents=(
                    int(row["balance_cents"]) if row.get("balance_cents") is not None
                    else (cents(row.get("balance")) or 0)
                ),
                status=row.get("status"),
                owner_name=row.get("owner_name") or row.get("owner"),
            )
            for row in self._rows
        ]
        return PortalBalanceSnapshot(
            building_id=building_id,
            snapshot_date=snapshot_date or date.today().isoformat(),
            financial_year=str(financial_year),
            source=self.name,
            raw_admin_fund_balance_cents=int(cents(self._admin) or 0),
            raw_sinking_fund_balance_cents=int(cents(self._sinking) or 0),
            raw_arrears_total_cents=int(cents(self._arrears) or 0),
            raw_credit_total_cents=int(cents(self._credit) or 0),
            raw_collection_rate=self._collection_rate,
            per_unit_balances=per_unit,
            is_test_data=self._is_test_data,
        )
