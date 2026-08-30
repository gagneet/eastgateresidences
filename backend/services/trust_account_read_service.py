# @featuretrace:pg-migration-shadow — Canonical Mongo read path for live trust V2.
# Layer: service
# Data flow: trust_phase1.py -> TrustAccountReadService -> trust_accounts_v2/trust_transactions_v2.
# Related: backend/routers/trust_phase1.py
#          backend/services/trust_shadow_read_service.py
#          tasks/GAP-TRUST-V2-SHADOW-READ-001-retarget-comparators-to-live-system.md
"""Mongo-backed trust V2 read models.

This service is the canonical read path for the live V2 trust system while the
domain remains Mongo-primary. It deliberately computes balances from immutable
transactions instead of trusting a static current-balance field.
"""
from __future__ import annotations

from typing import Any

from bson import ObjectId

from models.trust_accounting import format_aud


_CREDIT_TYPES = {"receipt", "interest"}
_DEBIT_TYPES = {"disbursement", "bank_charge", "reversal"}


class TrustAccountReadService:
    """Read trust V2 accounts and balances from MongoDB."""

    async def _transaction_totals(
        self,
        db: Any,
        *,
        building_id: str,
        account_id: ObjectId,
    ) -> dict[str, int]:
        """Generated function header.

        Function: TrustAccountReadService._transaction_totals
        Path: backend/services/trust_account_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        pipeline = [
            {
                "$match": {
                    "building_id": building_id,
                    "trust_account_id": account_id,
                    "is_reversed": False,
                }
            },
            {
                "$group": {
                    "_id": None,
                    "receipts": {
                        "$sum": {
                            "$cond": [{"$in": ["$type", sorted(_CREDIT_TYPES)]}, "$amount_cents", 0]
                        }
                    },
                    "disbursements": {
                        "$sum": {
                            "$cond": [{"$in": ["$type", sorted(_DEBIT_TYPES)]}, "$amount_cents", 0]
                        }
                    },
                }
            },
        ]
        rows = await db.trust_transactions_v2.aggregate(pipeline).to_list(1)
        if not rows:
            return {"receipts": 0, "disbursements": 0}
        row = rows[0]
        return {
            "receipts": int(row.get("receipts") or 0),
            "disbursements": int(row.get("disbursements") or 0),
        }

    async def computed_balance_cents(
        self,
        db: Any,
        *,
        building_id: str,
        account: dict[str, Any],
    ) -> int:
        """Generated function header.

        Function: TrustAccountReadService.computed_balance_cents
        Path: backend/services/trust_account_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        opening = int(account.get("opening_balance_cents") or 0)
        totals = await self._transaction_totals(
            db,
            building_id=building_id,
            account_id=account["_id"],
        )
        return opening + totals["receipts"] - totals["disbursements"]

    async def list_accounts(
        self,
        db: Any,
        *,
        building_id: str,
    ) -> list[dict[str, Any]]:
        """Generated function header.

        Function: TrustAccountReadService.list_accounts
        Path: backend/services/trust_account_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        docs = await db.trust_accounts_v2.find(
            {"building_id": building_id, "is_active": True}
        ).to_list(100)

        results: list[dict[str, Any]] = []
        for doc in docs:
            opening = int(doc.get("opening_balance_cents") or 0)
            computed_balance = await self.computed_balance_cents(
                db,
                building_id=building_id,
                account=doc,
            )
            last_rec = int(doc.get("last_reconciled_balance_cents") or 0)
            reconciliation_gap = computed_balance - last_rec
            results.append({
                "id": str(doc["_id"]),
                "building_id": str(doc.get("building_id", "")),
                "account_type": doc.get("account_type"),
                "bsb": doc.get("bsb"),
                "account_number_masked": doc.get("account_number_masked"),
                "bank_name": doc.get("bank_name"),
                "account_name": doc.get("account_name"),
                "account_category": doc.get("account_category", "transaction"),
                "deft_biller_code": doc.get("deft_biller_code"),
                "interest_rate_pa": doc.get("interest_rate_pa", 0.0),
                "interest_calc_method": doc.get("interest_calc_method", "daily_balance"),
                "term_deposit_maturity_date": doc.get("term_deposit_maturity_date"),
                "notes": doc.get("notes"),
                "computed_balance_cents": computed_balance,
                "computed_balance_display": format_aud(computed_balance),
                "current_balance_cents": computed_balance,
                "current_balance_display": format_aud(computed_balance),
                "opening_balance_cents": opening,
                "last_reconciled_at": doc.get("last_reconciled_at"),
                "last_reconciled_balance_cents": last_rec,
                "last_reconciled_balance_display": format_aud(last_rec),
                "reconciliation_gap_cents": reconciliation_gap,
                "reconciliation_gap_display": format_aud(reconciliation_gap),
                "interest_ytd_cents": int(doc.get("interest_ytd_cents") or 0),
                "interest_ytd_display": format_aud(int(doc.get("interest_ytd_cents") or 0)),
                "is_active": doc.get("is_active", True),
                "created_at": doc.get("created_at", ""),
                "updated_at": doc.get("updated_at", ""),
            })
        return results

    async def get_account_balance(
        self,
        db: Any,
        *,
        building_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustAccountReadService.get_account_balance
        Path: backend/services/trust_account_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        account = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
        if not account:
            return None
        if str(account.get("building_id")) != building_id:
            return {"not_accessible": True}

        opening = int(account.get("opening_balance_cents") or 0)
        computed_balance = await self.computed_balance_cents(
            db,
            building_id=building_id,
            account=account,
        )
        last_rec_balance = int(account.get("last_reconciled_balance_cents") or 0)
        reconciliation_gap = computed_balance - last_rec_balance
        uncleared = await db.trust_transactions_v2.count_documents({
            "trust_account_id": account["_id"],
            "building_id": building_id,
            "is_reconciled": False,
        })

        return {
            "computed_balance": {"cents": computed_balance, "display": format_aud(computed_balance)},
            "current_balance": {"cents": computed_balance, "display": format_aud(computed_balance)},
            "opening_balance": {"cents": opening, "display": format_aud(opening)},
            "last_reconciled_at": account.get("last_reconciled_at"),
            "last_reconciled_balance": {"cents": last_rec_balance, "display": format_aud(last_rec_balance)},
            "reconciliation_gap": {"cents": reconciliation_gap, "display": format_aud(reconciliation_gap)},
            "uncleared_count": uncleared,
        }

    async def list_transactions(
        self,
        db: Any,
        *,
        building_id: str,
        account_id: str,
        page: int,
        limit: int,
        transaction_type: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        is_reconciled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustAccountReadService.list_transactions
        Path: backend/services/trust_account_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        account = await db.trust_accounts_v2.find_one({"_id": ObjectId(account_id)})
        if not account:
            return None
        if str(account.get("building_id")) != building_id:
            return {"not_accessible": True}

        transaction_type = transaction_type if isinstance(transaction_type, str) and transaction_type else None
        is_reconciled = is_reconciled if isinstance(is_reconciled, bool) else None
        from_date = from_date if isinstance(from_date, str) and from_date else None
        to_date = to_date if isinstance(to_date, str) and to_date else None

        match: dict[str, Any] = {
            "building_id": building_id,
            "trust_account_id": ObjectId(account_id),
        }
        if transaction_type:
            match["type"] = transaction_type
        if is_reconciled is not None:
            match["is_reconciled"] = is_reconciled
        if from_date or to_date:
            match["created_at"] = {}
            if from_date:
                match["created_at"]["$gte"] = from_date
            if to_date:
                match["created_at"]["$lte"] = to_date

        skip = (page - 1) * limit
        docs = await (
            db.trust_transactions_v2.find(match)
            .sort("created_at", 1)
            .skip(skip)
            .limit(limit)
            .to_list(limit)
        )
        total = await db.trust_transactions_v2.count_documents(match)

        # BUG-TRUST-002: never fall back to current_balance_cents here. That field is
        # an ending/computed balance and using it as an opening anchor double-counts.
        #
        # If the visible page is date-filtered, rows before the filter window still
        # affect the true ledger balance. Seed the running total with those rows,
        # then add any earlier rows from the current filtered page window.
        running = int(account.get("opening_balance_cents") or 0)
        if from_date:
            pre_window_match = {
                key: value for key, value in match.items()
                if key != "created_at"
            }
            pre_window_match["created_at"] = {"$lt": from_date}
            pre_window = await (
                db.trust_transactions_v2.find(pre_window_match)
                .sort("created_at", 1)
                .to_list(None)
            )
            for tx in pre_window:
                running = _apply_transaction(running, tx)

        preceding = await (
            db.trust_transactions_v2.find(match)
            .sort("created_at", 1)
            .limit(skip)
            .to_list(skip or 0)
        )
        for tx in preceding:
            running = _apply_transaction(running, tx)

        results: list[dict[str, Any]] = []
        for doc in docs:
            running = _apply_transaction(running, doc)
            amount = int(doc.get("amount_cents") or 0)
            tx_type = doc.get("type")
            results.append({
                "id": str(doc["_id"]),
                "building_id": str(doc.get("building_id", "")),
                "trust_account_id": str(doc.get("trust_account_id", "")),
                "type": tx_type,
                "amount_cents": amount,
                "amount_display": format_aud(amount),
                "description": doc.get("description", ""),
                "reference": doc.get("reference"),
                "payee_payer": doc.get("payee_payer"),
                "payment_method": doc.get("payment_method"),
                "is_reconciled": doc.get("is_reconciled", False),
                "is_reversed": doc.get("is_reversed", False),
                "running_balance_cents": running,
                "running_balance_display": format_aud(running),
                "created_at": doc.get("created_at", ""),
            })

        return {
            "data": results,
            "pagination": {"page": page, "limit": limit, "total": total},
        }


def _apply_transaction(running: int, tx: dict[str, Any]) -> int:
    """Generated function header.

    Function: _apply_transaction
    Path: backend/services/trust_account_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    amount = int(tx.get("amount_cents") or 0)
    if tx.get("type") in _CREDIT_TYPES:
        return running + amount
    return running - amount
