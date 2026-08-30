"""
Shadow read validator for Phase F financial core migration.

Implements the shadow-read pattern: when financial_core.shadow_read_postgres
toggle is enabled, every read operation is executed against both Postgres and
MongoDB, and divergences are logged for manual investigation.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant

logger = logging.getLogger(__name__)


@dataclass
class ShadowReadResult:
    """Result of a shadow-read comparison."""

    query_type: str
    query_params: dict
    postgres_value: Any
    mongodb_value: Any
    diverged: bool
    divergence_summary: Optional[str] = None
    method_name: Optional[str] = None
    diverging_fields: list[str] | None = None
    is_test_data: bool = False

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "query_type": self.query_type,
            "method_name": self.method_name,
            "query_params": self.query_params,
            "postgres_value": self.postgres_value,
            "mongodb_value": self.mongodb_value,
            "diverged": self.diverged,
            "divergence_summary": self.divergence_summary,
            "diverging_fields": self.diverging_fields or [],
            "is_test_data": self.is_test_data,
        }


class ShadowReadValidator:
    """Validates Postgres reads against MongoDB (shadow) during migration."""

    def __init__(self, session=None, outbox_repo=None):
        """Generated function header.

        Function: ShadowReadValidator.__init__
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.session = session
        self.outbox_repo = outbox_repo

    async def validate_finance_summary(
        self,
        *,
        building_id: str,
        query_params: dict[str, Any],
        mongodb_value: Any,
        postgres_value: Any,
        is_test_data: bool = False,
    ) -> ShadowReadResult:
        """Compare /finance/summary response between MongoDB and Postgres paths.

        Only the budget figures and arrears are compared — actual-collection
        estimates differ by method (proportional split vs fund-allocation) and
        are expected to diverge even after full data migration.
        """
        method_name = "get_finance_summary"
        scheme = await config_repo.resolve_scheme_context(building_id)
        normalized_mongo = self._normalize_finance_summary(mongodb_value)
        normalized_pg = self._normalize_finance_summary(postgres_value)
        diverging_fields = self._diff_fields(normalized_pg, normalized_mongo)
        diverged = bool(diverging_fields)
        summary = (
            f"Finance summary divergence: {', '.join(diverging_fields[:8])}"
            if diverged else None
        )
        result = ShadowReadResult(
            query_type=method_name,
            method_name=method_name,
            query_params=query_params,
            postgres_value=normalized_pg,
            mongodb_value=normalized_mongo,
            diverged=diverged,
            divergence_summary=summary,
            diverging_fields=diverging_fields,
            is_test_data=is_test_data,
        )
        if diverged and scheme is not None:
            await self._log_divergence(scheme, result)
        return result

    async def validate_unit_levy_balance(
        self,
        *,
        building_id: str,
        query_params: dict[str, Any],
        mongodb_value: Any,
        postgres_value: Any,
        is_test_data: bool = False,
    ) -> ShadowReadResult:
        """Compare per-unit levy balance between MongoDB unit_levy_ledger and Postgres.

        MongoDB stores: total_levied, total_paid, net_balance.
        Postgres stores: levied_amount, paid_amount, closing_balance.
        Both are normalised to the same canonical keys before comparison.
        """
        method_name = "get_unit_levy_balance"
        scheme = await config_repo.resolve_scheme_context(building_id)
        normalized_mongo = self._normalize_unit_levy_balance(mongodb_value)
        normalized_pg = self._normalize_unit_levy_balance(postgres_value)
        diverging_fields = self._diff_fields(normalized_pg, normalized_mongo)
        diverged = bool(diverging_fields)
        summary = (
            f"Unit levy balance divergence: {', '.join(diverging_fields[:8])}"
            if diverged else None
        )
        result = ShadowReadResult(
            query_type=method_name,
            method_name=method_name,
            query_params=query_params,
            postgres_value=normalized_pg,
            mongodb_value=normalized_mongo,
            diverged=diverged,
            divergence_summary=summary,
            diverging_fields=diverging_fields,
            is_test_data=is_test_data,
        )
        if diverged and scheme is not None:
            await self._log_divergence(scheme, result)
        return result

    async def validate_trial_balance(
        self,
        *,
        building_id: str,
        query_params: dict[str, Any],
        mongodb_value: Any,
        postgres_value: Any,
        is_test_data: bool = False,
    ) -> ShadowReadResult:
        """Compare trial balance (account-level debits/credits) between MongoDB and Postgres.

        MongoDB has no native trial balance — the caller supplies whatever aggregate it
        has available (e.g. financial_transactions by GL code).  100% divergence is the
        expected baseline until Postgres finance tables are populated.
        """
        method_name = "get_trial_balance"
        scheme = await config_repo.resolve_scheme_context(building_id)
        normalized_mongo = self._normalize_trial_balance(mongodb_value)
        normalized_pg = self._normalize_trial_balance(postgres_value)
        diverging_fields = self._diff_fields(normalized_pg, normalized_mongo)
        diverged = bool(diverging_fields)
        summary = (
            f"Trial balance divergence: {', '.join(diverging_fields[:8])}"
            if diverged else None
        )
        result = ShadowReadResult(
            query_type=method_name,
            method_name=method_name,
            query_params=query_params,
            postgres_value=normalized_pg,
            mongodb_value=normalized_mongo,
            diverged=diverged,
            divergence_summary=summary,
            diverging_fields=diverging_fields,
            is_test_data=is_test_data,
        )
        if diverged and scheme is not None:
            await self._log_divergence(scheme, result)
        return result

    async def validate_trust_account_summary(
        self,
        *,
        building_id: str,
        query_params: dict[str, Any],
        mongodb_value: Any,
        postgres_value: Any,
        is_test_data: bool = False,
    ) -> ShadowReadResult:
        """Compare trust account balances between MongoDB trust_accounts_v2 and Postgres.

        Compares current_balance per fund_type.  Account names and metadata are excluded
        because they differ by data-entry convention between the two stores.
        """
        method_name = "get_trust_account_summary"
        scheme = await config_repo.resolve_scheme_context(building_id)
        normalized_mongo = self._normalize_trust_account_summary(mongodb_value)
        normalized_pg = self._normalize_trust_account_summary(postgres_value)
        diverging_fields = self._diff_fields(normalized_pg, normalized_mongo)
        diverged = bool(diverging_fields)
        summary = (
            f"Trust account summary divergence: {', '.join(diverging_fields[:8])}"
            if diverged else None
        )
        result = ShadowReadResult(
            query_type=method_name,
            method_name=method_name,
            query_params=query_params,
            postgres_value=normalized_pg,
            mongodb_value=normalized_mongo,
            diverged=diverged,
            divergence_summary=summary,
            diverging_fields=diverging_fields,
            is_test_data=is_test_data,
        )
        if diverged and scheme is not None:
            await self._log_divergence(scheme, result)
        return result

    async def validate_reconciliation_summary(
        self,
        *,
        building_id: str,
        query_params: dict[str, Any],
        mongodb_value: Any,
        postgres_value: Any,
        is_test_data: bool = False,
    ) -> ShadowReadResult:
        """Compare reconciliation summary counts and discrepancy between MongoDB and Postgres.

        Compares: matched_count, unmatched_bank_count,
        total_unreconciled_amount_cents, discrepancy_cents.
        """
        method_name = "get_reconciliation_summary"
        scheme = await config_repo.resolve_scheme_context(building_id)
        normalized_mongo = self._normalize_reconciliation_summary(mongodb_value)
        normalized_pg = self._normalize_reconciliation_summary(postgres_value)
        diverging_fields = self._diff_fields(normalized_pg, normalized_mongo)
        diverged = bool(diverging_fields)
        summary = (
            f"Reconciliation summary divergence: {', '.join(diverging_fields[:8])}"
            if diverged else None
        )
        result = ShadowReadResult(
            query_type=method_name,
            method_name=method_name,
            query_params=query_params,
            postgres_value=normalized_pg,
            mongodb_value=normalized_mongo,
            diverged=diverged,
            divergence_summary=summary,
            diverging_fields=diverging_fields,
            is_test_data=is_test_data,
        )
        if diverged and scheme is not None:
            await self._log_divergence(scheme, result)
        return result

    async def validate_owners(
        self,
        *,
        building_id: str,
        method_name: str,
        query_params: dict[str, Any],
        mongodb_value: Any,
        postgres_value: Any,
        is_test_data: bool = False,
    ) -> ShadowReadResult:
        """Generated function header.

        Function: ShadowReadValidator.validate_owners
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await config_repo.resolve_scheme_context(building_id)
        normalized_mongo = self._normalize_owner_payload(method_name, mongodb_value)
        normalized_pg = self._normalize_owner_payload(method_name, postgres_value)
        diverging_fields = self._diff_fields(normalized_pg, normalized_mongo)
        diverged = bool(diverging_fields)
        summary = None
        if diverged:
            summary = f"Owner divergence in {method_name}: {', '.join(diverging_fields[:8])}"

        result = ShadowReadResult(
            query_type=method_name,
            method_name=method_name,
            query_params=query_params,
            postgres_value=normalized_pg,
            mongodb_value=normalized_mongo,
            diverged=diverged,
            divergence_summary=summary,
            diverging_fields=diverging_fields,
            is_test_data=is_test_data,
        )
        if diverged and scheme is not None:
            await self._log_divergence(scheme, result)
        return result

    @staticmethod
    def _compare_values(value_a: Any, value_b: Any) -> Tuple[bool, Optional[str]]:
        """Generated function header.

        Function: ShadowReadValidator._compare_values
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            a_normalized = json.loads(json.dumps(value_a, default=str))
            b_normalized = json.loads(json.dumps(value_b, default=str))
            if a_normalized == b_normalized:
                return False, None
            summary = (
                f"Postgres: {json.dumps(a_normalized)[:200]}... "
                f"MongoDB: {json.dumps(b_normalized)[:200]}..."
            )
            return True, summary
        except Exception as exc:
            logger.warning(f"Failed to compare values: {exc}")
            return True, f"Comparison error: {str(exc)[:200]}"

    @staticmethod
    def _normalize_scalar(value: Any) -> Any:
        """Generated function header.

        Function: ShadowReadValidator._normalize_scalar
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if value in (None, "", [], {}):
            return None
        if isinstance(value, str):
            collapsed = " ".join(value.split()).strip()
            if "@" in collapsed:
                return collapsed.lower()
            return collapsed.title()
        if isinstance(value, list):
            return [ShadowReadValidator._normalize_scalar(item) for item in value]
        if isinstance(value, dict):
            return {key: ShadowReadValidator._normalize_scalar(val) for key, val in value.items()}
        return value

    @classmethod
    def _normalize_owner_payload(cls, method_name: str, value: Any) -> Any:
        """Generated function header.

        Function: ShadowReadValidator._normalize_owner_payload
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if method_name == "get_owner_info":
            payload = value or {}
            return {
                "owner_name": cls._normalize_scalar(payload.get("owner_name")),
                "owner_email": cls._normalize_scalar(payload.get("owner_email")),
                "owner_name_b": cls._normalize_scalar(payload.get("owner_name_b") or payload.get("co_owner_name")),
                "owner_email_b": cls._normalize_scalar(payload.get("owner_email_b") or payload.get("co_owner_email")),
            }

        if method_name == "get_all_unit_owners":
            payload = value or {}
            normalized: dict[str, Any] = {}
            for unit_number in sorted(payload):
                owner = payload[unit_number] or {}
                normalized[str(unit_number)] = {
                    "owner_name": cls._normalize_scalar(owner.get("owner_name")),
                    "owner_email": cls._normalize_scalar(owner.get("owner_email")),
                    "owner_name_b": cls._normalize_scalar(owner.get("owner_name_b") or owner.get("co_owner_name")),
                    "owner_email_b": cls._normalize_scalar(owner.get("owner_email_b") or owner.get("co_owner_email")),
                }
            return normalized

        if method_name == "resolve_agm_voters":
            payload = value or []
            normalized = []
            for owner in payload:
                normalized.append(
                    {
                        "unit_number": cls._normalize_scalar(owner.get("unit_number")),
                        "lot_number": cls._normalize_scalar(owner.get("lot_number")),
                        "full_name": cls._normalize_scalar(owner.get("full_name")),
                        "email": cls._normalize_scalar(owner.get("email")),
                        "ownership_share": str(owner.get("ownership_share")) if owner.get("ownership_share") is not None else None,
                        "is_primary_owner": bool(owner.get("is_primary_owner")),
                        "entitlement_units": str(owner.get("entitlement_units")) if owner.get("entitlement_units") is not None else None,
                    }
                )
            return sorted(
                normalized,
                key=lambda item: (
                    item.get("unit_number") or "",
                    item.get("lot_number") or "",
                    item.get("email") or "",
                    item.get("full_name") or "",
                ),
            )

        return cls._normalize_scalar(value)

    @staticmethod
    def _normalize_finance_summary(value: Any) -> dict[str, Any]:
        """Generated function header.

        Function: ShadowReadValidator._normalize_finance_summary
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not value:
            return {}
        # Only compare the authoritative budget figures and arrears.
        # Collection actuals intentionally excluded: MongoDB uses proportional
        # estimates; Postgres uses fund-allocation rows.  They will always differ
        # by rounding even after full migration.
        return {
            "admin_fund_budget": round(float(value.get("admin_fund_budget", 0) or 0), 2),
            "sinking_fund_budget": round(float(value.get("sinking_fund_budget", 0) or 0), 2),
            "total_income": round(float(value.get("total_income", 0) or 0), 2),
            "levy_arrears_total": round(float(value.get("levy_arrears_total", 0) or 0), 2),
        }

    @staticmethod
    def _normalize_unit_levy_balance(value: Any) -> dict[str, Any]:
        """Generated function header.

        Function: ShadowReadValidator._normalize_unit_levy_balance
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not value:
            return {}
        # MongoDB unit_levy_ledger uses total_levied / total_paid / net_balance.
        # Postgres uses levied_amount / paid_amount / closing_balance.
        # Canonical keys used on both sides so _diff_fields can compare them.
        levied = value.get("levied_amount") if value.get("levied_amount") is not None else value.get("total_levied", 0)
        paid = value.get("paid_amount") if value.get("paid_amount") is not None else value.get("total_paid", 0)
        closing = value.get("closing_balance") if value.get("closing_balance") is not None else value.get("net_balance", 0)
        return {
            "levied_amount": round(float(levied or 0), 2),
            "paid_amount": round(float(paid or 0), 2),
            "closing_balance": round(float(closing or 0), 2),
        }

    @staticmethod
    def _normalize_trial_balance(value: Any) -> dict[str, Any]:
        """Generated function header.

        Function: ShadowReadValidator._normalize_trial_balance
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not value:
            return {"accounts": {}, "total_debits": 0.0, "total_credits": 0.0, "is_balanced": True}
        accounts_list = value.get("accounts", [])
        accounts_map: dict[str, Any] = {}
        for acc in accounts_list:
            code = str(acc.get("account_code", ""))
            if code:
                accounts_map[code] = {
                    "debit": round(float(acc.get("debit", 0) or 0), 2),
                    "credit": round(float(acc.get("credit", 0) or 0), 2),
                }
        return {
            "accounts": accounts_map,
            "total_debits": round(float(value.get("total_debits", 0) or 0), 2),
            "total_credits": round(float(value.get("total_credits", 0) or 0), 2),
            "is_balanced": bool(value.get("is_balanced", True)),
        }

    @staticmethod
    def _normalize_trust_account_summary(value: Any) -> dict[str, Any]:
        """Generated function header.

        Function: ShadowReadValidator._normalize_trust_account_summary
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not value:
            return {}
        # Accept either a list of accounts or a dict with an 'accounts'/'data' key.
        accounts = (
            value
            if isinstance(value, list)
            else value.get("accounts", value.get("data", []))
        )
        summary: dict[str, Any] = {}
        for acc in (accounts or []):
            fund_type = str(acc.get("fund_type") or acc.get("account_type") or "unknown")
            current = acc.get("current_balance") or acc.get("balance") or 0
            # Aggregate by fund_type; multiple accounts of the same type are summed.
            prev = summary.get(fund_type, {}).get("current_balance", 0.0)
            summary[fund_type] = {"current_balance": round(float(prev) + float(current or 0), 2)}
        return summary

    @staticmethod
    def _normalize_reconciliation_summary(value: Any) -> dict[str, Any]:
        """Generated function header.

        Function: ShadowReadValidator._normalize_reconciliation_summary
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if value is None:
            return {}
        return {
            "matched_count": int(value.get("matched_count", 0) or 0),
            "unmatched_bank_count": int(value.get("unmatched_bank_count", 0) or 0),
            "total_unreconciled_amount_cents": int(
                value.get("total_unreconciled_amount_cents", 0) or 0
            ),
            "discrepancy_cents": int(value.get("discrepancy_cents", 0) or 0),
        }

    @classmethod
    def _diff_fields(cls, left: Any, right: Any, prefix: str = "") -> list[str]:
        """Generated function header.

        Function: ShadowReadValidator._diff_fields
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if isinstance(left, dict) and isinstance(right, dict):
            fields: list[str] = []
            for key in sorted(set(left) | set(right)):
                next_prefix = f"{prefix}.{key}" if prefix else key
                fields.extend(cls._diff_fields(left.get(key), right.get(key), next_prefix))
            return fields
        if isinstance(left, list) and isinstance(right, list):
            fields: list[str] = []
            max_len = max(len(left), len(right))
            for index in range(max_len):
                next_prefix = f"{prefix}[{index}]"
                left_item = left[index] if index < len(left) else None
                right_item = right[index] if index < len(right) else None
                fields.extend(cls._diff_fields(left_item, right_item, next_prefix))
            return fields
        if left != right:
            return [prefix or "result"]
        return []

    async def _log_divergence(self, scheme_ref, result: ShadowReadResult) -> None:
        """Generated function header.

        Function: ShadowReadValidator._log_divergence
        Path: backend/services/shadow_read_validator.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            from sqlalchemy import text

            tenant_id = getattr(scheme_ref, "tenant_id", None) or scheme_ref["tenant_id"]
            scheme_id = getattr(scheme_ref, "scheme_id", None) or scheme_ref["scheme_id"]
            params = {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "scheme_id": str(scheme_id) if scheme_id else None,
                "query_type": result.query_type,
                "method_name": result.method_name or result.query_type,
                "query_params": json.dumps(result.query_params, default=str),
                "pg_result": json.dumps(result.postgres_value, default=str),
                "mongo_result": json.dumps(result.mongodb_value, default=str),
                "summary": result.divergence_summary,
                "diverging_fields": result.diverging_fields or [],
                "is_test_data": result.is_test_data,
                "now": datetime.now(tz=timezone.utc),
            }
            statement = text(
                """
                INSERT INTO core.shadow_read_divergences
                (divergence_id, tenant_id, scheme_id, query_type, method_name, query_params,
                 postgres_result, mongodb_result, difference_summary, diverging_fields,
                 is_test_data, detected_at)
                VALUES (CAST(:id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                        :query_type, :method_name,
                        CAST(:query_params AS jsonb), CAST(:pg_result AS jsonb), CAST(:mongo_result AS jsonb),
                        :summary, :diverging_fields, :is_test_data, :now)
                """
            )

            if self.session is not None:
                await self.session.execute(statement, params)
            else:
                async with async_session_context() as session:
                    if tenant_id:
                        await set_tenant(session, str(tenant_id))
                    await session.execute(statement, params)

            logger.warning(
                "Shadow read divergence logged: type=%s, summary=%s",
                result.query_type,
                result.divergence_summary,
            )
        except Exception as exc:
            logger.error(f"Failed to log divergence: {exc}")
            raise
