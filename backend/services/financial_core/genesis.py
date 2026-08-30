# @featuretrace:financial_core — Per-scheme genesis: chart of accounts, funds, opening period, opening journal.
# Layer: service
# Data flow: onboarding flow / bootstrap scripts -> genesis.py -> finance.gl_accounts + finance.funds +
#            finance.accounting_periods + finance.journal_entries (building-scoped).
# Related: backend/services/financial_core/service.py
#          backend/services/financial_core/domain/entities.py (PostGenesisJournalCommand)
#          backend/alembic/versions/0072_recovered_fees_gl_account.py (4003 backfill onto existing schemes)
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import text

from db_postgres.session import set_tenant
from services import cutover_config_service
from services.financial_core import get_financial_core_service
from services.financial_core.domain.entities import (
    JournalEntry,
    PostGenesisJournalCommand,
    SchemeRef,
)

READ_FROM_POSTGRES_FEATURE_KEY = "financial_core.read_from_postgres"
SYSTEM_CUTOVER_USER_ID = UUID("00000000-0000-0000-0000-000000000321")
SYSTEM_CUTOVER_USER_NAME = "System Financial Cutover"
SYSTEM_CUTOVER_PASSWORD_HASH = "!"
# Only trusted scripted cutover flows may omit a real actor id and fall back to
# a deterministic system user. This guards against future call sites treating a
# missing user id as a general-purpose way to bypass actor attribution.
SCRIPTED_CUTOVER_ACTOR_ALLOWLIST = frozenset({SYSTEM_CUTOVER_USER_NAME})
DEFAULT_GL_ACCOUNTS = (
    ("1000", "Bank Account", "asset", True),
    ("1010", "Undeposited Funds", "asset", True),
    ("1100", "Accounts Receivable", "asset", True),
    ("3100", "Opening Balances Clearing", "equity", True),
    ("4000", "Levy Income", "income", False),
    # Backs FinancialCoreService.charge_lot_fee() — see migration
    # 0072_recovered_fees_gl_account (which back-fills this onto every
    # scheme onboarded before this line existed). Fund-specific GL accounts
    # used by the ledger writer live in DEFAULT_FUND_GL_ACCOUNTS below because
    # they must be created after finance.funds exists.
    ("4003", "Recovered Fees & Collection Costs", "income", False),
)
DEFAULT_FUNDS = (
    ("ADMIN", "Administrative Fund", "admin"),
    ("SINK", "Sinking Fund", "sinking"),
    ("SPECIAL", "Special Purpose Fund", "special_purpose"),
)
DEFAULT_FUND_GL_ACCOUNTS = (
    ("admin", "5000", "Administration Expenses", "expense", False),
    ("admin", "5001", "Insurance", "expense", False),
    ("admin", "5002", "Repairs and Maintenance", "expense", False),
    ("admin", "5003", "Management Fees", "expense", False),
    ("admin", "5004", "Legal and Compliance", "expense", False),
    ("sinking", "4001", "Sinking Levy Income", "income", False),
    ("sinking", "5100", "Sinking Fund Expenses", "expense", False),
)
DEFAULT_CANONICAL_CUTOVER_FEATURES = (
    cutover_config_service.UMBRELLA_FEATURE_KEY,
    cutover_config_service.FINANCIAL_PG_WRITES_ENABLED,
    cutover_config_service.FINANCIAL_PG_READS_ENABLED,
    cutover_config_service.BANK_INTEGRATION_ABSTRACTION_ENABLED,
    cutover_config_service.TRUST_PG_LEDGER_ENABLED,
    cutover_config_service.TRUST_RECONCILIATION_PG_ENABLED,
    cutover_config_service.ONBOARDING_CURRENT_BALANCE_ADAPTERS_ENABLED,
)
ALL_CANONICAL_CUTOVER_FEATURES = (
    cutover_config_service.UMBRELLA_FEATURE_KEY,
    cutover_config_service.FINANCIAL_PG_WRITES_ENABLED,
    cutover_config_service.FINANCIAL_PG_READS_ENABLED,
    cutover_config_service.FINANCIAL_SHADOW_READS_ENABLED,
    cutover_config_service.BANK_INTEGRATION_ABSTRACTION_ENABLED,
    cutover_config_service.TRUST_PG_LEDGER_ENABLED,
    cutover_config_service.TRUST_RECONCILIATION_PG_ENABLED,
    cutover_config_service.EXTERNAL_API_FINANCE_PG_ENABLED,
    cutover_config_service.ONBOARDING_CURRENT_BALANCE_ADAPTERS_ENABLED,
)
LEGACY_CUTOVER_FEATURE_KEYS = tuple(sorted(cutover_config_service.LEGACY_FEATURE_KEY_ALIASES.keys()))


def normalize_fund_type(raw_value: str) -> str:
    """Generated function header.

    Function: normalize_fund_type
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    value = (raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if "admin" in value:
        return "admin"
    if "sink" in value or "capital" in value:
        return "sinking"
    if "special" in value:
        return "special"
    raise ValueError(f"Unsupported fund type: {raw_value!r}")


def derive_building_id_from_scheme_number(scheme_number: str | None) -> str | None:
    """Generated function header.

    Function: derive_building_id_from_scheme_number
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not scheme_number:
        return None
    return str(scheme_number).upper().removeprefix("UP") or None


def compute_evidence_doc_hash(balance_row: Mapping[str, Any]) -> str:
    """Generated function header.

    Function: compute_evidence_doc_hash
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payload = {
        "fund_type": normalize_fund_type(str(balance_row.get("fund_type", ""))),
        "opening_balance_cents": int(balance_row.get("opening_balance_cents") or 0),
        "as_at_date": str(balance_row.get("as_at_date") or ""),
        "bsb": str(balance_row.get("bsb") or ""),
        "account_number": str(balance_row.get("account_number") or ""),
        "account_name": str(balance_row.get("account_name") or ""),
        "evidence_source": str(balance_row.get("evidence_source") or ""),
        "notes": str(balance_row.get("notes") or ""),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _mask_account_number(value: str) -> str | None:
    """Generated function header.

    Function: _mask_account_number
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return None
    return f"****{digits[-4:]}"


def _mask_bsb(value: str) -> str | None:
    """Generated function header.

    Function: _mask_bsb
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 3:
        return None
    return f"***-{digits[-3:]}"


def _derive_cutover_actor_email(*, building_id: str | None, scheme_ref: SchemeRef) -> str:
    """Generated function header.

    Function: _derive_cutover_actor_email
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scope = (building_id or str(scheme_ref.scheme_id)).strip().lower()
    return f"system-cutover+{scope}@system.strataos.local"


def _assert_scripted_actor_name_allowed(full_name: str) -> None:
    """Generated function header.

    Function: _assert_scripted_actor_name_allowed
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if full_name not in SCRIPTED_CUTOVER_ACTOR_ALLOWLIST:
        raise ValueError(
            "Scripted cutover actor fallback is only allowed for approved system actors. "
            f"Received {full_name!r}."
        )


async def ensure_cutover_actor_user(
        session,
        *,
        scheme_ref: SchemeRef,
        building_id: str | None,
        full_name: str = SYSTEM_CUTOVER_USER_NAME,
) -> UUID:
    """Generated function header.

    Function: ensure_cutover_actor_user
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _assert_scripted_actor_name_allowed(full_name)
    email = _derive_cutover_actor_email(building_id=building_id, scheme_ref=scheme_ref)
    deterministic_user_id = uuid5(
        NAMESPACE_URL,
        f"system-cutover:{scheme_ref.tenant_id}:{scheme_ref.scheme_id}:{email}",
    )
    result = await session.execute(
        text(
            """
            INSERT INTO core.users
                (user_id, tenant_id, email, full_name, first_name, last_name,
                 password_hash, role, default_scheme_id, is_active, is_approved)
            VALUES
                (:user_id, :tenant_id, :email, :full_name, :first_name, :last_name,
                 :password_hash, CAST(:role AS core.user_role), :default_scheme_id, TRUE, TRUE)
            ON CONFLICT (tenant_id, email)
            DO UPDATE
                SET full_name = EXCLUDED.full_name,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    role = EXCLUDED.role,
                    default_scheme_id = EXCLUDED.default_scheme_id,
                    is_active = TRUE,
                    is_approved = TRUE,
                    updated_at = NOW()
            RETURNING user_id::TEXT
            """
        ),
        {
            "user_id": str(deterministic_user_id),
            "tenant_id": str(scheme_ref.tenant_id),
            "email": email,
            "full_name": full_name,
            "first_name": "System",
            "last_name": "Financial Cutover",
            "password_hash": SYSTEM_CUTOVER_PASSWORD_HASH,
            "role": "strata_manager",
            "default_scheme_id": str(scheme_ref.scheme_id),
        },
    )
    user_id = result.scalar_one()
    await session.execute(
        text(
            """
            INSERT INTO core.user_role_assignments
                (tenant_id, user_id, scheme_id, role, is_active)
            VALUES
                (:tenant_id, :user_id, :scheme_id, CAST(:role AS core.user_role), TRUE)
            ON CONFLICT (user_id, scheme_id, role)
            WHERE scheme_id IS NOT NULL
            DO UPDATE SET is_active = TRUE, granted_at = NOW()
            """
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "user_id": user_id,
            "scheme_id": str(scheme_ref.scheme_id),
            "role": "strata_manager",
        },
    )
    return UUID(str(user_id))


async def ensure_scheme_finance_bootstrap(
        session,
        *,
        scheme_ref: SchemeRef,
        opening_balances: list[dict[str, Any]],
) -> None:
    """Generated function header.

    Function: ensure_scheme_finance_bootstrap
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    latest_as_at = max(
        datetime.fromisoformat(str(balance["as_at_date"])).date()
        for balance in opening_balances
    )
    period_start = date(latest_as_at.year, 1, 1)
    period_end = date(latest_as_at.year, 12, 31)
    period_label = str(latest_as_at.year)

    for fund_code, fund_name, fund_type in DEFAULT_FUNDS:
        await session.execute(
            text(
                """
                INSERT INTO finance.funds
                    (fund_id, tenant_id, scheme_id, fund_code, fund_name, fund_type, status, created_at)
                SELECT :fund_id, :tenant_id, :scheme_id, :fund_code, :fund_name,
                       CAST(:fund_type AS finance.fund_type), CAST('active' AS core.record_status), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.funds WHERE scheme_id = :scheme_id AND fund_code = :fund_code
                )
                """
            ),
            {
                "fund_id": str(uuid4()),
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "fund_code": fund_code,
                "fund_name": fund_name,
                "fund_type": fund_type,
            },
        )

    await session.execute(
        text(
            """
            INSERT INTO finance.accounting_periods
                (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on, status, created_at)
            SELECT :period_id, :tenant_id, :scheme_id, :period_label, :starts_on, :ends_on, 'open', NOW()
            WHERE NOT EXISTS (
                SELECT 1 FROM finance.accounting_periods
                WHERE scheme_id = :scheme_id AND period_label = :period_label
            )
            """
        ),
        {
            "period_id": str(uuid4()),
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "period_label": period_label,
            "starts_on": period_start,
            "ends_on": period_end,
        },
    )

    for account_code, account_name, account_type, is_control in DEFAULT_GL_ACCOUNTS:
        await session.execute(
            text(
                """
                INSERT INTO finance.gl_accounts
                    (gl_account_id, tenant_id, scheme_id, account_code, account_name, account_type, is_control_account, status, created_at)
                SELECT :gl_account_id, :tenant_id, :scheme_id, :account_code, :account_name,
                       CAST(:account_type AS finance.account_type), :is_control,
                       CAST('active' AS core.record_status), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.gl_accounts WHERE scheme_id = :scheme_id AND account_code = :account_code
                )
                """
            ),
            {
                "gl_account_id": str(uuid4()),
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "account_code": account_code,
                "account_name": account_name,
                "account_type": account_type,
                "is_control": is_control,
            },
        )

    fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)
    for fund_type, account_code, account_name, account_type, is_control in DEFAULT_FUND_GL_ACCOUNTS:
        await session.execute(
            text(
                """
                INSERT INTO finance.gl_accounts
                    (gl_account_id, tenant_id, scheme_id, fund_id, account_code,
                     account_name, account_type, is_control_account, status, created_at)
                SELECT :gl_account_id, :tenant_id, :scheme_id, :fund_id, :account_code,
                       :account_name, CAST(:account_type AS finance.account_type), :is_control,
                       CAST('active' AS core.record_status), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.gl_accounts
                    WHERE scheme_id = :scheme_id AND account_code = :account_code
                )
                """
            ),
            {
                "gl_account_id": str(uuid4()),
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "fund_id": str(fund_ids[fund_type]),
                "account_code": account_code,
                "account_name": account_name,
                "account_type": account_type,
                "is_control": is_control,
            },
        )
    for balance in opening_balances:
        fund_type = normalize_fund_type(str(balance.get("fund_type", "")))
        await session.execute(
            text(
                """
                INSERT INTO finance.trust_accounts
                    (trust_account_id, tenant_id, scheme_id, fund_id, bank_name, account_name,
                     masked_bsb, masked_account_number, status, created_at)
                SELECT :trust_account_id, :tenant_id, :scheme_id, :fund_id, :bank_name, :account_name,
                       :masked_bsb, :masked_account_number, CAST('active' AS core.record_status), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.trust_accounts
                    WHERE scheme_id = :scheme_id AND fund_id = :fund_id AND COALESCE(status, 'active') = 'active'
                )
                """
            ),
            {
                "trust_account_id": str(uuid4()),
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "fund_id": str(fund_ids[fund_type]),
                "bank_name": str(balance.get("bank_name") or balance.get("evidence_source") or "Imported Trust Account"),
                "account_name": str(balance.get("account_name") or f"{fund_type.title()} Trust Account"),
                "masked_bsb": _mask_bsb(str(balance.get("bsb") or "")),
                "masked_account_number": _mask_account_number(str(balance.get("account_number") or "")),
            },
        )


async def resolve_scheme_fund_ids(session, scheme_ref: SchemeRef) -> dict[str, UUID]:
    """Generated function header.

    Function: resolve_scheme_fund_ids
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            "SELECT fund_id::TEXT, fund_type, fund_code, fund_name "
            "FROM finance.funds "
            "WHERE scheme_id = :scheme_id AND COALESCE(status, 'active') = 'active'"
        ),
        {"scheme_id": str(scheme_ref.scheme_id)},
    )
    rows = result.fetchall()
    mapping: dict[str, UUID] = {}
    for row in rows:
        for candidate in (row.fund_type, row.fund_code, row.fund_name):
            try:
                mapping.setdefault(normalize_fund_type(candidate), UUID(str(row.fund_id)))
            except ValueError:
                continue
    missing = {"admin", "sinking"} - set(mapping)
    if missing:
        raise ValueError(f"Missing finance.funds rows for: {', '.join(sorted(missing))}")
    return mapping


async def enable_postgres_read_override(
        session,
        *,
        scheme_ref: SchemeRef,
        set_by_user_id: UUID,
        reason: str,
) -> None:
    """Generated function header.

    Function: enable_postgres_read_override
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            "INSERT INTO core.feature_toggle_overrides "
            "    (tenant_id, scheme_id, feature_key, is_enabled, set_by, reason) "
            "VALUES (:tenant_id, :scheme_id, :feature_key, true, :set_by, :reason) "
            "ON CONFLICT (scheme_id, feature_key) DO UPDATE "
            "SET is_enabled = EXCLUDED.is_enabled, set_by = EXCLUDED.set_by, "
            "    set_at = NOW(), reason = EXCLUDED.reason"
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "feature_key": READ_FROM_POSTGRES_FEATURE_KEY,
            "set_by": str(set_by_user_id),
            "reason": reason,
        },
    )


async def _upsert_feature_toggle_override(
        session,
        *,
        scheme_ref: SchemeRef,
        feature_key: str,
        is_enabled: bool,
        set_by_user_id: UUID | None,
        reason: str,
) -> None:
    """Generated function header.

    Function: _upsert_feature_toggle_override
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            "INSERT INTO core.feature_toggle_overrides "
            "    (tenant_id, scheme_id, feature_key, is_enabled, set_by, reason) "
            "VALUES (:tenant_id, :scheme_id, :feature_key, :is_enabled, :set_by, :reason) "
            "ON CONFLICT (scheme_id, feature_key) DO UPDATE "
            "SET is_enabled = EXCLUDED.is_enabled, "
            "    set_by = COALESCE(EXCLUDED.set_by, core.feature_toggle_overrides.set_by), "
            "    set_at = NOW(), "
            "    reason = EXCLUDED.reason"
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "feature_key": feature_key,
            "is_enabled": bool(is_enabled),
            "set_by": str(set_by_user_id) if set_by_user_id else None,
            "reason": reason,
        },
    )


async def _delete_feature_toggle_override(
        session,
        *,
        scheme_ref: SchemeRef,
        feature_key: str,
) -> None:
    """Generated function header.

    Function: _delete_feature_toggle_override
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            "DELETE FROM core.feature_toggle_overrides "
            "WHERE tenant_id = :tenant_id "
            "  AND scheme_id = :scheme_id "
            "  AND feature_key = :feature_key"
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "feature_key": feature_key,
        },
    )


async def _upsert_building_setting(
        session,
        *,
        scheme_ref: SchemeRef,
        setting_key: str,
        setting_value: Any,
        set_by_user_id: UUID | None,
) -> None:
    """Generated function header.

    Function: _upsert_building_setting
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await session.execute(
        text(
            """
            INSERT INTO core.building_settings
                (tenant_id, scheme_id, setting_key, setting_value, set_by, set_at)
            VALUES
                (:tenant_id, :scheme_id, :setting_key, CAST(:setting_value AS jsonb), :set_by, NOW())
            ON CONFLICT (scheme_id, setting_key)
            DO UPDATE
                SET setting_value = EXCLUDED.setting_value,
                    set_by = COALESCE(EXCLUDED.set_by, core.building_settings.set_by),
                    set_at = NOW()
            """
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "setting_key": setting_key,
            "setting_value": json.dumps(setting_value, default=str),
            "set_by": str(set_by_user_id) if set_by_user_id else None,
        },
    )


async def enable_canonical_cutover_overrides(
        session,
        *,
        scheme_ref: SchemeRef,
        set_by_user_id: UUID | None,
        building_id: str | None,
        reason: str,
        bank_provider: str = "mock",
        bank_mode: str = "mock",
        enable_external_api_finance: bool = False,
        enable_shadow_reads: bool = False,
) -> list[str]:
    """Generated function header.

    Function: enable_canonical_cutover_overrides
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    effective_set_by_user_id = set_by_user_id or await ensure_cutover_actor_user(
        session,
        scheme_ref=scheme_ref,
        building_id=building_id,
    )
    enabled_features = list(DEFAULT_CANONICAL_CUTOVER_FEATURES)
    if enable_external_api_finance:
        enabled_features.append(cutover_config_service.EXTERNAL_API_FINANCE_PG_ENABLED)
    if enable_shadow_reads:
        enabled_features.append(cutover_config_service.FINANCIAL_SHADOW_READS_ENABLED)

    for feature_key in ALL_CANONICAL_CUTOVER_FEATURES:
        if feature_key not in enabled_features:
            await _delete_feature_toggle_override(
                session,
                scheme_ref=scheme_ref,
                feature_key=feature_key,
            )

    for feature_key in enabled_features:
        await _upsert_feature_toggle_override(
            session,
            scheme_ref=scheme_ref,
            feature_key=feature_key,
            is_enabled=True,
            set_by_user_id=effective_set_by_user_id,
            reason=reason,
        )

    await _upsert_building_setting(
        session,
        scheme_ref=scheme_ref,
        setting_key=cutover_config_service.BANKING_PROVIDER_SETTING_KEY,
        setting_value=bank_provider,
        set_by_user_id=effective_set_by_user_id,
    )
    await _upsert_building_setting(
        session,
        scheme_ref=scheme_ref,
        setting_key=cutover_config_service.BANKING_MODE_SETTING_KEY,
        setting_value=bank_mode,
        set_by_user_id=effective_set_by_user_id,
    )

    for legacy_feature_key in LEGACY_CUTOVER_FEATURE_KEYS:
        await _delete_feature_toggle_override(
            session,
            scheme_ref=scheme_ref,
            feature_key=legacy_feature_key,
        )

    return enabled_features


async def record_cutover_in_session_step_data(
        session,
        *,
        session_id: str,
        initiated_by_user_id: str,
        session_tenant_id: str,
        cutover_record: dict[str, Any],
) -> None:
    """Generated function header.

    Function: record_cutover_in_session_step_data
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await set_tenant(session, session_tenant_id)
    await session.execute(
        text(
            "UPDATE core.onboarding_sessions "
            "SET step_data = step_data || CAST(:patch AS JSONB), updated_at = NOW() "
            "WHERE session_id = :session_id AND initiated_by = :initiated_by"
        ),
        {
            "patch": json.dumps({"cutover": cutover_record}),
            "session_id": session_id,
            "initiated_by": initiated_by_user_id,
        },
    )


async def post_import_based_genesis_cutover(
        session,
        *,
        scheme_ref: SchemeRef,
        opening_balances: list[dict[str, Any]],
        posted_by_user_id: UUID | None,
        posted_by_user_name: str,
        building_id: str | None,
        is_test_data: bool = False,
        enable_external_api_finance: bool = False,
        enable_shadow_reads: bool = False,
        bank_provider: str = "mock",
        bank_mode: str = "mock",
) -> tuple[list[JournalEntry], dict[str, Any]]:
    """Generated function header.

    Function: post_import_based_genesis_cutover
    Path: backend/services/financial_core/genesis.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not opening_balances:
        raise ValueError("No opening balances were captured for import-based genesis posting")

    await set_tenant(session, str(scheme_ref.tenant_id))
    await ensure_scheme_finance_bootstrap(
        session,
        scheme_ref=scheme_ref,
        opening_balances=opening_balances,
    )
    effective_posted_by_user_id = posted_by_user_id or await ensure_cutover_actor_user(
        session,
        scheme_ref=scheme_ref,
        building_id=building_id,
        full_name=posted_by_user_name,
    )
    fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)
    financial_core = get_financial_core_service(session)
    posted_entries: list[JournalEntry] = []

    for balance in opening_balances:
        fund_type = normalize_fund_type(str(balance.get("fund_type", "")))
        as_at_date = datetime.fromisoformat(str(balance["as_at_date"])).date()
        evidence_doc_id = balance.get("evidence_doc_id")
        cmd = PostGenesisJournalCommand(
            scheme_ref=scheme_ref,
            fund_id=fund_ids[fund_type],
            opening_balance_cents=int(balance["opening_balance_cents"]),
            as_at_date=as_at_date,
            evidence_doc_id=UUID(str(evidence_doc_id)) if evidence_doc_id else None,
            evidence_doc_hash=compute_evidence_doc_hash(balance),
            posted_by_user_id=effective_posted_by_user_id,
            approved_by_user_id=effective_posted_by_user_id,
            posted_by_user_name=posted_by_user_name,
            building_id=building_id,
            idempotency_key=f"genesis:{scheme_ref.scheme_id}:{fund_type}:{as_at_date.isoformat()}",
            is_test_data=is_test_data,
        )
        posted_entries.append(await financial_core.post_genesis_journal(cmd))

    reason = (
        f"Canonical financial cutover enabled after genesis on {datetime.now(tz=timezone.utc).date().isoformat()}"
    )
    enabled_features = await enable_canonical_cutover_overrides(
        session,
        scheme_ref=scheme_ref,
        set_by_user_id=effective_posted_by_user_id,
        building_id=building_id,
        reason=reason,
        bank_provider=bank_provider,
        bank_mode=bank_mode,
        enable_external_api_finance=enable_external_api_finance,
        enable_shadow_reads=enable_shadow_reads,
    )

    cutover_record = {
        "canonical_cutover_enabled": True,
        "cutover_recorded_at": datetime.now(tz=timezone.utc).isoformat(),
        "building_id": building_id,
        "enabled_feature_keys": enabled_features,
        "legacy_feature_keys_removed": list(LEGACY_CUTOVER_FEATURE_KEYS),
        "banking_provider": bank_provider,
        "banking_mode": bank_mode,
        "funds": [
            {
                "fund_type": normalize_fund_type(str(balance.get("fund_type", ""))),
                "fund_id": str(fund_ids[normalize_fund_type(str(balance.get('fund_type', '')))]),
                "opening_balance_cents": int(balance["opening_balance_cents"]),
                "as_at_date": str(balance["as_at_date"]),
                "evidence_doc_hash": compute_evidence_doc_hash(balance),
            }
            for balance in opening_balances
        ],
    }
    return posted_entries, cutover_record
