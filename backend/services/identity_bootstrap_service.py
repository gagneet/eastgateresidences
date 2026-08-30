# @featuretrace:postgres-identity-foundation — Idempotent bootstrap of tenant/scheme/lot/owner/user foundations.
# Layer: service
# Data flow: bootstrap CLI (fixture|mongo) → IdentityBootstrapService → core.tenants/schemes/buildings/lots/users/parties/ownership_periods/user_units/user_role_assignments (building-scoped).
# Related: backend/scripts/bootstrap_postgres_identity_foundation.py
#          backend/scripts/postgres_cutover_p0_readiness.py
#          backend/services/cutover_status_service.py
#          tests/backend/test_identity_bootstrap_service.py
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import json
import re
from typing import Any, AsyncIterator, Callable
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db_postgres.session import async_session_context, set_tenant
from models.user import ECPosition, UserRole, normalize_user_role


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCHEME_NAMESPACE = UUID("4f2c9010-36e5-4b35-b5aa-2e80d36f9f37")
_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"
_VALID_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
    UserRole.ADMIN_STAFF,
    UserRole.OWNER,
    UserRole.TENANT,
    UserRole.REAL_ESTATE_AGENT,
    UserRole.SERVICE_PROVIDER,
    UserRole.GUEST,
}
_VALID_JURISDICTIONS = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}
_VALID_EC_POSITIONS = {
    ECPosition.CHAIRMAN,
    ECPosition.TREASURER,
    ECPosition.SECRETARY,
    ECPosition.MEMBER,
}


class ValidationError(ValueError):
    """Raised when bootstrap input is invalid."""


class BootstrapMode(str, Enum):
    DRY_RUN = "dry_run"
    APPLY = "apply"
    VALIDATE_ONLY = "validate_only"


class BootstrapSource(str, Enum):
    MONGO = "mongo"
    FIXTURE = "fixture"


@dataclass(slots=True)
class LotRecord:
    lot_number: str
    unit_number: str
    lot_use: str = "residential"
    entitlement_units: float | None = None
    source_record_id: str | None = None
    source_external_id: str | None = None

    @property
    def idempotency_key(self) -> str:
        """Generated function header.

        Function: LotRecord.idempotency_key
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return f"{self.lot_number.strip()}::{(self.unit_number or '').strip()}"


@dataclass(slots=True)
class OwnerRecord:
    full_name: str
    email: str | None
    lot_numbers: list[str]
    ownership_start: date
    ownership_end: date | None = None
    source_record_id: str | None = None
    source_external_id: str | None = None

    def __post_init__(self) -> None:
        """Generated function header.

        Function: OwnerRecord.__post_init__
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self.ownership_end and self.ownership_end <= self.ownership_start:
            raise ValueError("ownership_end must be after ownership_start")


@dataclass(slots=True)
class UserRecord:
    email: str
    full_name: str
    role: str = UserRole.OWNER
    ec_position: str | None = None
    phone: str | None = None
    source_record_id: str | None = None
    source_external_id: str | None = None
    password_hash: str | None = None

    @property
    def normalized_email(self) -> str:
        """Generated function header.

        Function: UserRecord.normalized_email
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return (self.email or "").strip().lower()


@dataclass(slots=True)
class IdentityFixture:
    plan_number: str
    building_slug: str
    building_name: str
    jurisdiction: str
    lots: list[LotRecord]
    owners: list[OwnerRecord] = field(default_factory=list)
    users: list[UserRecord] = field(default_factory=list)
    tenant_name: str | None = None
    source_system: str = "fixture"
    source_collection: str | None = None
    source_external_id: str | None = None


@dataclass(slots=True)
class BootstrapReport:
    mode: BootstrapMode
    dry_run: bool
    plan_number: str
    building_slug: str
    import_batch_id: str | None = None
    source: BootstrapSource = BootstrapSource.FIXTURE

    tenant_found: bool = False
    tenant_created: bool = False
    scheme_found: bool = False
    scheme_created: bool = False
    building_found: bool = False
    building_created: bool = False

    lots_found: int = 0
    lots_to_create: int = 0
    lots_created: int = 0
    lots_updated: int = 0

    users_found: int = 0
    users_to_create: int = 0
    users_created: int = 0
    users_skipped: int = 0

    owners_to_create: int = 0
    parties_found: int = 0
    parties_created: int = 0
    ownership_records_created: int = 0
    memberships_created: int = 0
    role_assignments_created: int = 0

    duplicate_users: list[str] = field(default_factory=list)
    duplicate_lots: list[str] = field(default_factory=list)
    units_without_owner: list[str] = field(default_factory=list)
    owners_without_lot: list[str] = field(default_factory=list)
    invalid_emails: list[str] = field(default_factory=list)
    missing_roles: list[str] = field(default_factory=list)
    skipped_records: list[str] = field(default_factory=list)
    cross_building_risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: BootstrapReport.to_dict
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["source"] = self.source.value
        return payload


def _utc_now() -> datetime:
    """Generated function header.

    Function: _utc_now
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(UTC)


def _normalize_plan_number(value: str) -> str:
    """Generated function header.

    Function: _normalize_plan_number
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = (value or "").strip()
    if raw.upper().startswith("UP") and len(raw) > 2:
        return raw[2:]
    return raw


def _canonical_lot_number(value: str | None) -> str:
    """Generated function header.

    Function: _canonical_lot_number
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = str(value or "").strip()
    match = re.fullmatch(r"LOT0*([0-9]+)", raw, flags=re.IGNORECASE)
    return match.group(1) if match else raw


def _scheme_number_candidates(building_id: str) -> list[str]:
    """Generated function header.

    Function: _scheme_number_candidates
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    normalized = (building_id or "").strip()
    if not normalized:
        return []
    bare = _normalize_plan_number(normalized)
    candidates = {normalized, bare, f"UP{bare}"}
    return sorted(c for c in candidates if c)


def _validate_email(email: str | None) -> bool:
    """Generated function header.

    Function: _validate_email
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not email:
        return False
    return bool(_EMAIL_RE.fullmatch(email.strip()))


def _slugify(value: str) -> str:
    """Generated function header.

    Function: _slugify
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "unknown-building"


def _stable_uuid(kind: str, *parts: str) -> str:
    """Generated function header.

    Function: _stable_uuid
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    clean = "::".join((kind, *(str(p or "").strip().lower() for p in parts)))
    return str(uuid5(_SCHEME_NAMESPACE, clean))


def _normalise_role(role: str | None) -> str | None:
    """Generated function header.

    Function: _normalise_role
    Path: backend/services/identity_bootstrap_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    normalized = normalize_user_role((role or "").strip())
    if normalized == "ec_member":
        # Legacy records still surface this slug in some Mongo datasets.
        return UserRole.EC_MEMBER
    if normalized == "chairman":
        # Legacy Mongo users may still carry role="chairman". Map to ec_member
        # during bootstrap so they are not silently skipped.
        return UserRole.EC_MEMBER
    if normalized == "building_admin":
        return UserRole.STRATA_ADMIN
    if normalized not in _VALID_ROLES:
        return None
    return normalized


class IdentityBootstrapService:
    """Build PostgreSQL identity foundation for a building/scheme."""

    def __init__(
        self,
        *,
        pg_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Generated function header.

        Function: IdentityBootstrapService.__init__
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._pg_session_factory = pg_session_factory

    def _detect_duplicate_lots(self, lots: list[LotRecord]) -> list[str]:
        """Generated function header.

        Function: IdentityBootstrapService._detect_duplicate_lots
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        seen: set[str] = set()
        duplicates: list[str] = []
        for lot in lots:
            key = (lot.lot_number or "").strip()
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        return duplicates

    def _detect_duplicate_users(self, users: list[UserRecord]) -> list[str]:
        """Generated function header.

        Function: IdentityBootstrapService._detect_duplicate_users
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        seen: set[str] = set()
        duplicates: list[str] = []
        for user in users:
            key = user.normalized_email
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        return duplicates

    def _validate_fixture(self, fixture: IdentityFixture) -> None:
        """Generated function header.

        Function: IdentityBootstrapService._validate_fixture
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not fixture.plan_number or not str(fixture.plan_number).strip():
            raise ValidationError("plan_number is required")
        if not fixture.lots:
            raise ValidationError("lots must contain at least one record")
        if fixture.jurisdiction not in _VALID_JURISDICTIONS:
            raise ValidationError("jurisdiction must be a valid Australian code")

    def _analyse_fixture(
        self,
        fixture: IdentityFixture,
        *,
        mode: BootstrapMode,
        import_batch_id: str | None,
        source: BootstrapSource,
    ) -> BootstrapReport:
        """Generated function header.

        Function: IdentityBootstrapService._analyse_fixture
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        report = BootstrapReport(
            mode=mode,
            dry_run=mode != BootstrapMode.APPLY,
            plan_number=fixture.plan_number,
            building_slug=fixture.building_slug,
            import_batch_id=import_batch_id,
            source=source,
            lots_to_create=len(fixture.lots),
            owners_to_create=len(fixture.owners),
        )
        report.duplicate_lots = self._detect_duplicate_lots(fixture.lots)
        report.duplicate_users = self._detect_duplicate_users(fixture.users)

        lot_numbers = {(lot.lot_number or "").strip() for lot in fixture.lots}
        owned_lots: set[str] = set()

        for owner in fixture.owners:
            for lot_number in owner.lot_numbers:
                normalized = (lot_number or "").strip()
                if not normalized:
                    continue
                if normalized in lot_numbers:
                    owned_lots.add(normalized)
                else:
                    report.owners_without_lot.append(owner.full_name)

            if owner.email and not _validate_email(owner.email):
                report.invalid_emails.append(owner.email)

        report.units_without_owner = sorted(lot_numbers - owned_lots)

        valid_user_emails: set[str] = set()
        for user in fixture.users:
            email = user.normalized_email
            if not _validate_email(email):
                report.invalid_emails.append(user.email)
                report.users_skipped += 1
                continue
            role = _normalise_role(user.role)
            if role is None:
                report.missing_roles.append(user.role)
                report.users_skipped += 1
                continue
            valid_user_emails.add(email)

        report.invalid_emails = sorted(set(report.invalid_emails))
        report.missing_roles = sorted(set(report.missing_roles))
        report.users_to_create = len(valid_user_emails)
        return report

    @asynccontextmanager
    async def _session_context(self) -> AsyncIterator[Any]:
        """Generated function header.

        Function: IdentityBootstrapService._session_context
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if self._pg_session_factory is None:
            async with async_session_context() as session:
                yield session
            return
        async with self._pg_session_factory() as session:
            yield session

    async def run(
        self,
        *,
        fixture: IdentityFixture,
        mode: BootstrapMode,
        source: BootstrapSource = BootstrapSource.FIXTURE,
        import_batch_id: str | None = None,
    ) -> BootstrapReport:
        """Generated function header.

        Function: IdentityBootstrapService.run
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._validate_fixture(fixture)
        report = self._analyse_fixture(
            fixture,
            mode=mode,
            import_batch_id=import_batch_id,
            source=source,
        )

        if mode != BootstrapMode.APPLY:
            return report

        batch_id = import_batch_id or _stable_uuid(
            "identity-import-batch",
            fixture.plan_number,
            fixture.building_slug,
            datetime.now(UTC).isoformat(timespec="seconds"),
        )

        async with self._session_context() as session:
            applied = await self._apply_to_db(fixture, batch_id, session)
        merged = self._merge_reports(report, applied)
        await self._record_cutover_readiness(merged, foundation_pass=self._is_foundation_valid(merged))
        return merged

    def _is_foundation_valid(self, report: BootstrapReport) -> bool:
        """Generated function header.

        Function: IdentityBootstrapService._is_foundation_valid
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return not (
            report.duplicate_lots
            or report.missing_roles
            or report.cross_building_risks
            or report.owners_without_lot
            or report.users_to_create == 0
            or report.lots_to_create == 0
        )

    def _merge_reports(self, base: BootstrapReport, applied: BootstrapReport) -> BootstrapReport:
        """Generated function header.

        Function: IdentityBootstrapService._merge_reports
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        merged = BootstrapReport(
            mode=base.mode,
            dry_run=base.dry_run,
            plan_number=base.plan_number,
            building_slug=base.building_slug,
            import_batch_id=base.import_batch_id or applied.import_batch_id,
            source=base.source,
        )
        for field_name in (
            "tenant_found", "tenant_created", "scheme_found", "scheme_created", "building_found", "building_created",
            "lots_found", "lots_to_create", "lots_created", "lots_updated",
            "users_found", "users_to_create", "users_created", "users_skipped",
            "owners_to_create", "parties_found", "parties_created", "ownership_records_created",
            "memberships_created", "role_assignments_created",
        ):
            setattr(merged, field_name, getattr(applied, field_name) or getattr(base, field_name))

        merged.duplicate_users = sorted(set(base.duplicate_users + applied.duplicate_users))
        merged.duplicate_lots = sorted(set(base.duplicate_lots + applied.duplicate_lots))
        merged.units_without_owner = sorted(set(base.units_without_owner + applied.units_without_owner))
        merged.owners_without_lot = sorted(set(base.owners_without_lot + applied.owners_without_lot))
        merged.invalid_emails = sorted(set(base.invalid_emails + applied.invalid_emails))
        merged.missing_roles = sorted(set(base.missing_roles + applied.missing_roles))
        merged.skipped_records = base.skipped_records + applied.skipped_records
        merged.cross_building_risks = sorted(set(base.cross_building_risks + applied.cross_building_risks))
        return merged

    async def _table_columns(self, session: Any, table_name: str) -> set[str]:
        """Generated function header.

        Function: IdentityBootstrapService._table_columns
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        result = await session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'core' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        return {row[0] for row in result.fetchall()}

    async def _record_cutover_readiness(self, report: BootstrapReport, foundation_pass: bool) -> None:
        """Generated function header.

        Function: IdentityBootstrapService._record_cutover_readiness
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            from services import cutover_status_service as cutover_svc

            await cutover_svc.record_identity_foundation_readiness(
                building_id=str(report.plan_number),
                validation_passed=foundation_pass and report.lots_created + report.lots_found > 0,
                summary=report.to_dict(),
                reason=(
                    "identity foundation bootstrap completed"
                    if foundation_pass else
                    "identity foundation validated without apply"
                ),
            )
        except Exception:
            # Bootstrap must not fail if cutover status snapshot write fails.
            return

    async def _resolve_scheme_context(self, session: Any, fixture: IdentityFixture) -> dict[str, str]:
        """Generated function header.

        Function: IdentityBootstrapService._resolve_scheme_context
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        candidates = _scheme_number_candidates(fixture.plan_number)
        where_parts: list[str] = []
        params: dict[str, Any] = {}
        for idx, candidate in enumerate(candidates):
            key = f"candidate_{idx}"
            where_parts.append(f"lower(s.scheme_number) = lower(:{key})")
            params[key] = candidate

        where_sql = " OR ".join(where_parts) if where_parts else "FALSE"
        result = await session.execute(
            text(
                f"""
                SELECT s.scheme_id::text, s.tenant_id::text
                FROM core.schemes s
                WHERE ({where_sql})
                ORDER BY s.updated_at DESC
                LIMIT 1
                """
            ),
            params,
        )
        row = result.fetchone()
        if row:
            return {"scheme_id": row[0], "tenant_id": row[1]}

        canonical_plan = _normalize_plan_number(fixture.plan_number)
        tenant_id = _stable_uuid("tenant", canonical_plan)
        scheme_id = _stable_uuid("scheme", canonical_plan)
        tenant_name = fixture.tenant_name or f"{fixture.building_name} Owners Corporation"

        await set_tenant(session, tenant_id)
        await session.execute(
            text(
                """
                INSERT INTO core.tenants (
                    tenant_id, tenant_name, tenant_type, status, source_system, source_collection,
                    source_record_id, import_batch_id, imported_at
                ) VALUES (
                    CAST(:tenant_id AS UUID), :tenant_name, 'strata_manager', 'active',
                    :source_system, :source_collection, :source_record_id, :import_batch_id, :imported_at
                )
                ON CONFLICT (tenant_id) DO UPDATE SET
                    tenant_name = EXCLUDED.tenant_name,
                    updated_at = now(),
                    source_system = COALESCE(EXCLUDED.source_system, core.tenants.source_system),
                    source_collection = COALESCE(EXCLUDED.source_collection, core.tenants.source_collection),
                    source_record_id = COALESCE(EXCLUDED.source_record_id, core.tenants.source_record_id),
                    import_batch_id = COALESCE(EXCLUDED.import_batch_id, core.tenants.import_batch_id),
                    imported_at = COALESCE(EXCLUDED.imported_at, core.tenants.imported_at)
                """
            ),
            {
                "tenant_id": tenant_id,
                "tenant_name": tenant_name,
                "source_system": fixture.source_system,
                "source_collection": fixture.source_collection,
                "source_record_id": fixture.source_external_id,
                "import_batch_id": None,
                "imported_at": _utc_now(),
            },
        )

        await session.execute(
            text(
                """
                INSERT INTO core.schemes (
                    scheme_id, tenant_id, jurisdiction, scheme_number, scheme_name, status,
                    source_system, source_collection, source_record_id, import_batch_id, imported_at
                ) VALUES (
                    CAST(:scheme_id AS UUID), CAST(:tenant_id AS UUID), :jurisdiction, :scheme_number, :scheme_name, 'active',
                    :source_system, :source_collection, :source_record_id, :import_batch_id, :imported_at
                )
                ON CONFLICT (scheme_id) DO UPDATE SET
                    scheme_name = EXCLUDED.scheme_name,
                    jurisdiction = EXCLUDED.jurisdiction,
                    updated_at = now(),
                    source_system = COALESCE(EXCLUDED.source_system, core.schemes.source_system),
                    source_collection = COALESCE(EXCLUDED.source_collection, core.schemes.source_collection),
                    source_record_id = COALESCE(EXCLUDED.source_record_id, core.schemes.source_record_id),
                    import_batch_id = COALESCE(EXCLUDED.import_batch_id, core.schemes.import_batch_id),
                    imported_at = COALESCE(EXCLUDED.imported_at, core.schemes.imported_at)
                """
            ),
            {
                "scheme_id": scheme_id,
                "tenant_id": tenant_id,
                "jurisdiction": fixture.jurisdiction,
                "scheme_number": canonical_plan,
                "scheme_name": fixture.building_name,
                "source_system": fixture.source_system,
                "source_collection": fixture.source_collection,
                "source_record_id": fixture.source_external_id,
                "import_batch_id": None,
                "imported_at": _utc_now(),
            },
        )
        return {"scheme_id": scheme_id, "tenant_id": tenant_id}

    async def _apply_to_db(self, fixture: IdentityFixture, batch_id: str, session: Any) -> BootstrapReport:
        """Generated function header.

        Function: IdentityBootstrapService._apply_to_db
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        report = BootstrapReport(
            mode=BootstrapMode.APPLY,
            dry_run=False,
            plan_number=fixture.plan_number,
            building_slug=fixture.building_slug,
            import_batch_id=batch_id,
            source=BootstrapSource.FIXTURE if fixture.source_system == "fixture" else BootstrapSource.MONGO,
            lots_to_create=len(fixture.lots),
            owners_to_create=len(fixture.owners),
        )
        await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": _BYPASS_UUID})
        scheme_ctx = await self._resolve_scheme_context(session, fixture)
        scheme_id = scheme_ctx["scheme_id"]
        tenant_id = scheme_ctx["tenant_id"]
        await set_tenant(session, tenant_id)

        scheme_row = await session.execute(
            text("SELECT scheme_id::text, tenant_id::text FROM core.schemes WHERE scheme_id = CAST(:sid AS UUID)"),
            {"sid": scheme_id},
        )
        report.scheme_found = scheme_row.fetchone() is not None
        report.scheme_created = not report.scheme_found
        report.tenant_found = True

        building_id = _stable_uuid("building", _normalize_plan_number(fixture.plan_number))
        building_asset_profile = json.dumps(
            {
                "building_slug": fixture.building_slug,
                "import_batch_id": batch_id,
                "source_system": fixture.source_system,
            }
        )
        existing_building = await session.execute(
            text("SELECT building_id::text FROM core.buildings WHERE building_id = CAST(:building_id AS UUID)"),
            {"building_id": building_id},
        )
        report.building_found = existing_building.fetchone() is not None
        await session.execute(
            text(
                """
                INSERT INTO core.buildings (
                    building_id, tenant_id, scheme_id, building_name, state, status, asset_profile
                ) VALUES (
                    CAST(:building_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), :building_name, :state, 'active',
                    CAST(:asset_profile AS JSONB)
                )
                ON CONFLICT (building_id) DO UPDATE SET
                    building_name = EXCLUDED.building_name,
                    state = EXCLUDED.state,
                    scheme_id = EXCLUDED.scheme_id,
                    tenant_id = EXCLUDED.tenant_id,
                    asset_profile = EXCLUDED.asset_profile,
                    updated_at = now()
                """
            ),
            {
                "building_id": building_id,
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "building_name": fixture.building_name,
                "state": fixture.jurisdiction,
                "asset_profile": building_asset_profile,
            },
        )
        report.building_created = not report.building_found
        report.tenant_created = False

        lots_by_number: dict[str, str] = {}
        for lot in fixture.lots:
            lot_number = (lot.lot_number or "").strip()
            if not lot_number:
                report.skipped_records.append("lot skipped: missing lot_number")
                continue
            lot_id = _stable_uuid("lot", scheme_id, lot_number)
            existing_lot = await session.execute(
                text(
                    """
                    SELECT lot_id::text, unit_number, lot_use, entitlement_units
                    FROM core.lots
                    WHERE scheme_id = CAST(:scheme_id AS UUID) AND lot_number = :lot_number
                    LIMIT 1
                    """
                ),
                {"scheme_id": scheme_id, "lot_number": lot_number},
            )
            lot_row = existing_lot.fetchone()
            if lot_row:
                report.lots_found += 1
                lots_by_number[lot_number] = str(lot_row[0])
                if lot.unit_number:
                    lots_by_number[str(lot.unit_number).strip()] = str(lot_row[0])
                if (
                    str(lot_row[1] or "") != str(lot.unit_number or "")
                    or str(lot_row[2] or "residential") != str(lot.lot_use or "residential")
                    or (
                        lot.entitlement_units is not None
                        and str(lot_row[3] or "") != str(lot.entitlement_units)
                    )
                ):
                    await session.execute(
                        text(
                            """
                            UPDATE core.lots
                            SET unit_number = :unit_number,
                                lot_use = :lot_use,
                                entitlement_units = :entitlement_units,
                                updated_at = now(),
                                source_system = :source_system,
                                source_collection = :source_collection,
                                source_record_id = :source_record_id,
                                import_batch_id = :import_batch_id,
                                imported_at = :imported_at,
                                metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:metadata AS JSONB)
                            WHERE lot_id = CAST(:lot_id AS UUID)
                            """
                        ),
                        {
                            "lot_id": lot_row[0],
                            "unit_number": lot.unit_number,
                            "lot_use": lot.lot_use or "residential",
                            "entitlement_units": lot.entitlement_units,
                            "source_system": fixture.source_system,
                            "source_collection": fixture.source_collection,
                            "source_record_id": lot.source_record_id or lot.source_external_id,
                            "import_batch_id": batch_id,
                            "imported_at": _utc_now(),
                            "metadata": json.dumps({"source_external_id": lot.source_external_id}),
                        },
                    )
                    report.lots_updated += 1
                continue

            await session.execute(
                text(
                    """
                    INSERT INTO core.lots (
                        lot_id, tenant_id, scheme_id, building_id, lot_number, unit_number, lot_use,
                        entitlement_units, status, source_system, source_collection, source_record_id,
                        import_batch_id, imported_at, metadata
                    ) VALUES (
                        CAST(:lot_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), CAST(:building_id AS UUID), :lot_number,
                        :unit_number, :lot_use, :entitlement_units, 'active', :source_system,
                        :source_collection, :source_record_id, :import_batch_id, :imported_at,
                        CAST(:metadata AS JSONB)
                    )
                    ON CONFLICT (lot_id) DO UPDATE SET
                        unit_number = EXCLUDED.unit_number,
                        lot_use = EXCLUDED.lot_use,
                        entitlement_units = EXCLUDED.entitlement_units,
                        source_system = EXCLUDED.source_system,
                        source_collection = EXCLUDED.source_collection,
                        source_record_id = EXCLUDED.source_record_id,
                        import_batch_id = EXCLUDED.import_batch_id,
                        imported_at = EXCLUDED.imported_at,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    """
                ),
                {
                    "lot_id": lot_id,
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "building_id": building_id,
                    "lot_number": lot_number,
                    "unit_number": lot.unit_number,
                    "lot_use": lot.lot_use or "residential",
                    "entitlement_units": lot.entitlement_units,
                    "source_system": fixture.source_system,
                    "source_collection": fixture.source_collection,
                    "source_record_id": lot.source_record_id or lot.source_external_id,
                    "import_batch_id": batch_id,
                    "imported_at": _utc_now(),
                    "metadata": json.dumps({"source_external_id": lot.source_external_id}),
                },
            )
            report.lots_created += 1
            lots_by_number[lot_number] = lot_id
            if lot.unit_number:
                lots_by_number[str(lot.unit_number).strip()] = lot_id

        users_by_email: dict[str, str] = {}
        for user in fixture.users:
            email = user.normalized_email
            if not _validate_email(email):
                report.invalid_emails.append(user.email)
                report.users_skipped += 1
                report.skipped_records.append(f"user skipped: invalid email '{user.email}'")
                continue

            role = _normalise_role(user.role)
            if role is None:
                report.missing_roles.append(user.role)
                report.users_skipped += 1
                report.skipped_records.append(f"user skipped: unsupported role '{user.role}'")
                continue

            user_id = _stable_uuid("user", tenant_id, email)
            existing_user = await session.execute(
                text(
                    """
                    SELECT user_id::text
                    FROM core.users
                    WHERE tenant_id = CAST(:tenant_id AS UUID) AND email = :email
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "email": email},
            )
            user_row = existing_user.fetchone()
            if user_row:
                report.users_found += 1
                users_by_email[email] = str(user_row[0])
            else:
                report.users_created += 1
                users_by_email[email] = user_id

            # password_hash: carry over the real Mongo hash when the source user has
            # one (portal accounts with a real login). Falls back to '' only for
            # legal-owner records with no portal account, which correctly forces a
            # password-reset flow rather than a silent, permanently-unusable login.
            # On conflict (an already-migrated user), only overwrite with a real,
            # non-empty hash -- never regress an existing real hash back to empty
            # by re-running this against a fixture snapshot that happens to lack it.
            await session.execute(
                text(
                    """
                    INSERT INTO core.users (
                        user_id, tenant_id, email, full_name, password_hash, role, effective_role,
                        default_scheme_id, is_active, is_approved, is_test_data, status
                    ) VALUES (
                        CAST(:user_id AS UUID), CAST(:tenant_id AS UUID), :email, :full_name, :password_hash, CAST(:role AS core.user_role),
                        CAST(:role AS core.user_role), CAST(:scheme_id AS UUID), TRUE, TRUE, FALSE, 'active'
                    )
                    ON CONFLICT (tenant_id, email) DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        role = EXCLUDED.role,
                        effective_role = EXCLUDED.effective_role,
                        default_scheme_id = EXCLUDED.default_scheme_id,
                        password_hash = CASE
                            WHEN COALESCE(EXCLUDED.password_hash, '') <> ''
                            THEN EXCLUDED.password_hash
                            ELSE core.users.password_hash
                        END,
                        is_active = TRUE,
                        updated_at = now()
                    """
                ),
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "email": email,
                    "full_name": user.full_name,
                    "password_hash": user.password_hash or "",
                    "role": role,
                    "scheme_id": scheme_id,
                },
            )

            assignment_exists = await session.execute(
                text(
                    """
                    SELECT assignment_id::text
                    FROM core.user_role_assignments
                    WHERE tenant_id = CAST(:tenant_id AS UUID)
                      AND user_id = CAST(:user_id AS UUID)
                      AND scheme_id = CAST(:scheme_id AS UUID)
                      AND role = CAST(:role AS core.user_role)
                      AND is_active = TRUE
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "user_id": users_by_email[email], "scheme_id": scheme_id, "role": role},
            )
            desired_ec_position = (
                user.ec_position
                if (role == UserRole.EC_MEMBER and user.ec_position in _VALID_EC_POSITIONS)
                else None
            )
            existing_assignment = assignment_exists.fetchone()
            if not existing_assignment:
                await session.execute(
                    text(
                        """
                        INSERT INTO core.user_role_assignments (
                            assignment_id, tenant_id, user_id, scheme_id, role, ec_position, is_active
                        ) VALUES (
                            CAST(:assignment_id AS UUID), CAST(:tenant_id AS UUID), CAST(:user_id AS UUID), CAST(:scheme_id AS UUID),
                            CAST(:role AS core.user_role), :ec_position, TRUE
                        )
                        """
                    ),
                    {
                        "assignment_id": _stable_uuid("role-assignment", users_by_email[email], scheme_id, role),
                        "tenant_id": tenant_id,
                        "user_id": users_by_email[email],
                        "scheme_id": scheme_id,
                        "role": role,
                        "ec_position": desired_ec_position,
                    },
                )
                report.role_assignments_created += 1
            elif desired_ec_position:
                await session.execute(
                    text(
                        """
                        UPDATE core.user_role_assignments
                        SET ec_position = :ec_position
                        WHERE assignment_id = CAST(:assignment_id AS UUID)
                          AND COALESCE(ec_position, '') <> :ec_position
                        """
                    ),
                    {
                        "assignment_id": existing_assignment[0],
                        "ec_position": desired_ec_position,
                    },
                )

        for owner in fixture.owners:
            owner_party_id = _stable_uuid(
                "party",
                tenant_id,
                owner.source_external_id or owner.email or owner.full_name,
            )
            owner_email = (owner.email or "").strip().lower() or None
            if owner_email and not _validate_email(owner_email):
                report.invalid_emails.append(owner.email or "")
                owner_email = None

            existing_party = await session.execute(
                text(
                    """
                    SELECT party_id::text
                    FROM core.parties
                    WHERE tenant_id = CAST(:tenant_id AS UUID)
                      AND (
                        (source_external_id IS NOT NULL AND source_external_id = :source_external_id)
                        OR (primary_email IS NOT NULL AND primary_email = :primary_email)
                        OR (lower(legal_name) = lower(:legal_name))
                      )
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "source_external_id": owner.source_external_id,
                    "primary_email": owner_email,
                    "legal_name": owner.full_name,
                },
            )
            party_row = existing_party.fetchone()
            if party_row:
                report.parties_found += 1
                owner_party_id = str(party_row[0])
            else:
                report.parties_created += 1

            await session.execute(
                text(
                    """
                    INSERT INTO core.parties (
                        party_id, tenant_id, party_type, legal_name, primary_email,
                        metadata, status, source_system, source_collection, source_record_id,
                        source_external_id, import_batch_id, imported_at
                    ) VALUES (
                        CAST(:party_id AS UUID), CAST(:tenant_id AS UUID), 'individual', :legal_name, :primary_email,
                        CAST(:metadata AS JSONB), 'active', :source_system, :source_collection, :source_record_id,
                        :source_external_id, :import_batch_id, :imported_at
                    )
                    ON CONFLICT (party_id) DO UPDATE SET
                        legal_name = EXCLUDED.legal_name,
                        primary_email = COALESCE(EXCLUDED.primary_email, core.parties.primary_email),
                        metadata = EXCLUDED.metadata,
                        source_system = EXCLUDED.source_system,
                        source_collection = EXCLUDED.source_collection,
                        source_record_id = COALESCE(EXCLUDED.source_record_id, core.parties.source_record_id),
                        source_external_id = COALESCE(EXCLUDED.source_external_id, core.parties.source_external_id),
                        import_batch_id = EXCLUDED.import_batch_id,
                        imported_at = EXCLUDED.imported_at,
                        updated_at = now()
                    """
                ),
                {
                    "party_id": owner_party_id,
                    "tenant_id": tenant_id,
                    "legal_name": owner.full_name,
                    "primary_email": owner_email,
                    "metadata": json.dumps({"source": fixture.source_system}),
                    "source_system": fixture.source_system,
                    "source_collection": fixture.source_collection,
                    "source_record_id": owner.source_record_id,
                    "source_external_id": owner.source_external_id,
                    "import_batch_id": batch_id,
                    "imported_at": _utc_now(),
                },
            )

            user_id = users_by_email.get(owner_email or "")
            for lot_number in owner.lot_numbers:
                normalized_lot = (lot_number or "").strip()
                lot_id = lots_by_number.get(normalized_lot)
                if not lot_id:
                    report.owners_without_lot.append(owner.full_name)
                    report.skipped_records.append(
                        f"ownership skipped: owner '{owner.full_name}' has missing lot '{normalized_lot}'"
                    )
                    continue

                # Match on owner+lot alone, not the bootstrap's own generic
                # date(2000,1,1) default -- a more precise ownership_period may
                # already exist for this owner on this lot (e.g. from the real
                # migration's actual transfer dates), and the exclusion
                # constraint ownership_periods_lot_owner_period_excl rejects
                # any overlapping range for the same (lot, owner) even when the
                # exact dates differ. If the owner already has ANY current
                # period here, the more precise existing data wins and the
                # bootstrap's generic fallback is redundant -- skip it instead
                # of crashing the whole apply run (found live 2026-07-25: lot
                # 63/UA063 is jointly owned by Anthony McDonald and Rose
                # Marimon, both already correctly dated from 2020-12-01, and
                # the bootstrap's 2000-01-01 default overlapped Anthony's own
                # real row).
                existing_ownership = await session.execute(
                    text(
                        """
                        SELECT ownership_period_id::text
                        FROM core.ownership_periods
                        WHERE tenant_id = CAST(:tenant_id AS UUID)
                          AND scheme_id = CAST(:scheme_id AS UUID)
                          AND lot_id = CAST(:lot_id AS UUID)
                          AND owner_party_id = CAST(:owner_party_id AS UUID)
                          AND recorded_to IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "scheme_id": scheme_id,
                        "lot_id": lot_id,
                        "owner_party_id": owner_party_id,
                    },
                )
                if not existing_ownership.fetchone():
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                text(
                                    """
                                    INSERT INTO core.ownership_periods (
                                        ownership_period_id, tenant_id, scheme_id, lot_id, owner_party_id,
                                        valid_from, valid_to, source_document_id, notes, source_system, source_collection,
                                        source_record_id, import_batch_id, imported_at
                                    ) VALUES (
                                        CAST(:ownership_period_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), CAST(:lot_id AS UUID),
                                        CAST(:owner_party_id AS UUID), :valid_from, :valid_to, :source_document_id, :notes,
                                        :source_system, :source_collection, :source_record_id, :import_batch_id, :imported_at
                                    )
                                    ON CONFLICT (ownership_period_id) DO NOTHING
                                    """
                                ),
                                {
                                    "ownership_period_id": _stable_uuid(
                                        "ownership-period",
                                        scheme_id,
                                        lot_id,
                                        owner_party_id,
                                        owner.ownership_start.isoformat(),
                                        (owner.ownership_end.isoformat() if owner.ownership_end else "open"),
                                    ),
                                    "tenant_id": tenant_id,
                                    "scheme_id": scheme_id,
                                    "lot_id": lot_id,
                                    "owner_party_id": owner_party_id,
                                    "valid_from": owner.ownership_start,
                                    "valid_to": owner.ownership_end,
                                    "source_document_id": owner.source_record_id,
                                    "notes": "identity bootstrap",
                                    "source_system": fixture.source_system,
                                    "source_collection": fixture.source_collection,
                                    "source_record_id": owner.source_record_id,
                                    "import_batch_id": batch_id,
                                    "imported_at": _utc_now(),
                                },
                            )
                        report.ownership_records_created += 1
                    except IntegrityError as exc:
                        # Defense in depth: a same-owner overlap not caught by
                        # the pre-check above (e.g. a partial/closed period
                        # this owner already holds on the same lot under a
                        # different date range) should not abort the whole
                        # fixture apply -- more precise existing data wins.
                        conflict_detail = str(exc.orig) if exc.orig is not None else str(exc)
                        report.skipped_records.append(
                            f"ownership skipped: lot '{normalized_lot}' owner '{owner.full_name}' "
                            f"conflicts with an existing ownership period ({conflict_detail})"
                        )

                if user_id:
                    user_unit_exists = await session.execute(
                        text(
                            """
                            SELECT user_unit_id::text
                            FROM core.user_units
                            WHERE tenant_id = CAST(:tenant_id AS UUID)
                              AND scheme_id = CAST(:scheme_id AS UUID)
                              AND user_id = CAST(:user_id AS UUID)
                              AND lot_id = CAST(:lot_id AS UUID)
                              AND relationship = 'owner'
                              AND valid_from = :valid_from
                              AND valid_to IS NULL
                            LIMIT 1
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "scheme_id": scheme_id,
                            "user_id": user_id,
                            "lot_id": lot_id,
                            "valid_from": owner.ownership_start,
                        },
                    )
                    if not user_unit_exists.fetchone():
                        await session.execute(
                            text(
                                """
                                INSERT INTO core.user_units (
                                    user_unit_id, tenant_id, scheme_id, user_id, lot_id, party_id, relationship,
                                    valid_from, valid_to, is_test_data
                                ) VALUES (
                                    CAST(:user_unit_id AS UUID), CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID), CAST(:user_id AS UUID),
                                    CAST(:lot_id AS UUID), CAST(:party_id AS UUID), 'owner', :valid_from, NULL, FALSE
                                )
                                """
                            ),
                            {
                                "user_unit_id": _stable_uuid(
                                    "user-unit",
                                    user_id,
                                    lot_id,
                                    owner.ownership_start.isoformat(),
                                ),
                                "tenant_id": tenant_id,
                                "scheme_id": scheme_id,
                                "user_id": user_id,
                                "lot_id": lot_id,
                                "party_id": owner_party_id,
                                "valid_from": owner.ownership_start,
                            },
                        )
                        report.memberships_created += 1

        report.invalid_emails = sorted(set(report.invalid_emails))
        report.owners_without_lot = sorted(set(report.owners_without_lot))
        return report

    async def load_fixture_from_mongo(
        self,
        *,
        building_id: str,
        plan_number: str | None = None,
        building_slug: str | None = None,
    ) -> IdentityFixture:
        """Generated function header.

        Function: IdentityBootstrapService.load_fixture_from_mongo
        Path: backend/services/identity_bootstrap_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        from database import db
        from request_context import get_ctx_building_id, set_ctx_building_id

        previous_building_id = get_ctx_building_id()
        set_ctx_building_id(building_id)
        try:
            building_filter = {"$or": [{"building_id": building_id}, {"plan_id": building_id}]}
            building_doc = await db.buildings.find_one(
                {"$or": [building_filter, {"plan_number": _normalize_plan_number(plan_number or building_id)}]},
                {"_id": 0, "building_name": 1, "name": 1, "state": 1, "building_slug": 1, "plan_number": 1},
            )

            units = await db.units.find(
                building_filter,
                {
                    "_id": 1,
                    "lot_number": 1,
                    "unit_number": 1,
                    "entitlement_units": 1,
                    "lot_use": 1,
                    "owner_name": 1,
                    "owner_email": 1,
                    "owner_name_b": 1,
                    "owner_email_b": 1,
                },
            ).to_list(None)
            if not units:
                raise ValidationError(f"No units found in Mongo for building '{building_id}'")

            lots = [
                LotRecord(
                    lot_number=_canonical_lot_number(unit.get("lot_number") or unit.get("unit_number")),
                    unit_number=str(unit.get("unit_number") or unit.get("lot_number") or "").strip(),
                    lot_use=str(unit.get("lot_use") or "residential"),
                    entitlement_units=float(unit["entitlement_units"]) if unit.get("entitlement_units") is not None else None,
                    source_record_id=str(unit.get("_id")),
                )
                for unit in units
            ]
            lot_number_by_unit_number = {
                lot.unit_number: lot.lot_number
                for lot in lots
                if lot.unit_number and lot.lot_number
            }

            mappings = await db.user_units.find(
                {**building_filter, "is_active": True},
                {"_id": 0, "user_id": 1, "unit_number": 1, "role_at_unit": 1, "start_date": 1, "end_date": 1},
            ).to_list(None)
            user_ids = sorted({m.get("user_id") for m in mappings if m.get("user_id")})
            users_docs = await db.users.find(
                {"id": {"$in": user_ids}},
                {
                    "_id": 0, "id": 1, "email": 1, "full_name": 1, "role": 1, "phone": 1,
                    "password_hash": 1, "hashed_password": 1,
                },
            ).to_list(None)
            users_by_id = {doc["id"]: doc for doc in users_docs if doc.get("id")}

            users: list[UserRecord] = []
            seen_email: set[str] = set()
            owners_by_email: dict[str, OwnerRecord] = {}
            for mapping in mappings:
                user_doc = users_by_id.get(mapping.get("user_id"))
                if not user_doc:
                    continue
                email = (user_doc.get("email") or "").strip().lower()
                full_name = (user_doc.get("full_name") or "").strip() or email
                role = _normalise_role(user_doc.get("role")) or UserRole.OWNER
                if email not in seen_email:
                    users.append(
                        UserRecord(
                            email=email,
                            full_name=full_name,
                            role=role,
                            phone=user_doc.get("phone"),
                            source_record_id=user_doc.get("id"),
                            password_hash=user_doc.get("password_hash") or user_doc.get("hashed_password") or None,
                        )
                    )
                    seen_email.add(email)

                if role not in {UserRole.OWNER, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER}:
                    continue
                owner = owners_by_email.get(email)
                if owner is None:
                    owner = OwnerRecord(
                        full_name=full_name,
                        email=email,
                        lot_numbers=[],
                        ownership_start=date(2000, 1, 1),
                        source_record_id=user_doc.get("id"),
                    )
                    owners_by_email[email] = owner
                mapped_unit_number = str(mapping.get("unit_number") or "").strip()
                lot_number = lot_number_by_unit_number.get(mapped_unit_number, mapped_unit_number)
                if lot_number and lot_number not in owner.lot_numbers:
                    owner.lot_numbers.append(lot_number)

            # East Gate's imported strata roll carries the broad owner email
            # set on units.owner_email / owner_email_b. Those are legal owner
            # records even when the owner has not yet activated a portal user
            # or no Mongo user_units row exists, so include them in the PG
            # identity foundation instead of shrinking PG to portal accounts.
            for unit in units:
                mapped_unit_number = str(unit.get("unit_number") or "").strip()
                lot_number = lot_number_by_unit_number.get(
                    mapped_unit_number,
                    _canonical_lot_number(unit.get("lot_number") or mapped_unit_number),
                )
                for name_key, email_key, label in (
                    ("owner_name", "owner_email", "primary"),
                    ("owner_name_b", "owner_email_b", "secondary"),
                ):
                    email = (unit.get(email_key) or "").strip().lower()
                    if not email:
                        continue
                    full_name = (unit.get(name_key) or "").strip() or email
                    owner = owners_by_email.get(email)
                    if owner is None:
                        owner = OwnerRecord(
                            full_name=full_name,
                            email=email,
                            lot_numbers=[],
                            ownership_start=date(2000, 1, 1),
                            source_record_id=str(unit.get("_id")) if unit.get("_id") else None,
                            source_external_id=f"unit:{mapped_unit_number}:{label}",
                        )
                        owners_by_email[email] = owner
                    if lot_number and lot_number not in owner.lot_numbers:
                        owner.lot_numbers.append(lot_number)
                    if email not in seen_email:
                        users.append(
                            UserRecord(
                                email=email,
                                full_name=full_name,
                                role=UserRole.OWNER,
                                source_record_id=str(unit.get("_id")) if unit.get("_id") else None,
                                source_external_id=f"unit:{mapped_unit_number}:{label}",
                            )
                        )
                        seen_email.add(email)

            # Operational staff (strata_manager, strata_admin, admin_staff) have no
            # lot and no user_units mapping, so the owner-centric passes above never
            # see them -- they were missing from every East Gate identity bootstrap
            # run until this was found (2026-07-25). Query db.users directly for this
            # building's non-owner staff roles instead of relying on unit ownership.
            staff_docs = await db.users.find(
                {
                    **building_filter,
                    "role": {"$in": ["strata_manager", "strata_admin", "admin_staff"]},
                    "is_active": {"$ne": False},
                },
                {
                    "_id": 0, "id": 1, "email": 1, "full_name": 1, "role": 1, "phone": 1,
                    "password_hash": 1, "hashed_password": 1,
                },
            ).to_list(None)
            for staff in staff_docs:
                email = (staff.get("email") or "").strip().lower()
                if not email or email in seen_email:
                    continue
                role = _normalise_role(staff.get("role")) or UserRole.STRATA_ADMIN
                users.append(
                    UserRecord(
                        email=email,
                        full_name=(staff.get("full_name") or "").strip() or email,
                        role=role,
                        phone=staff.get("phone"),
                        source_record_id=staff.get("id"),
                        password_hash=staff.get("password_hash") or staff.get("hashed_password") or None,
                    )
                )
                seen_email.add(email)

            resolved_plan = (
                str(plan_number or building_doc.get("plan_number") or _normalize_plan_number(building_id)).strip()
                if building_doc else _normalize_plan_number(plan_number or building_id)
            )
            resolved_name = (
                str(building_doc.get("building_name") or building_doc.get("name") or f"Building {resolved_plan}")
                if building_doc else f"Building {resolved_plan}"
            )
            resolved_slug = (
                str(building_slug or (building_doc.get("building_slug") if building_doc else "") or _slugify(resolved_name))
            )
            resolved_state = str((building_doc.get("state") if building_doc else None) or "ACT").upper()
            jurisdiction = resolved_state if resolved_state in _VALID_JURISDICTIONS else "ACT"


            return IdentityFixture(
                plan_number=resolved_plan,
                building_slug=resolved_slug,
                building_name=resolved_name,
                jurisdiction=jurisdiction,
                lots=lots,
                owners=list(owners_by_email.values()),
                users=users,
                source_system="mongo",
                source_collection="users,user_units,units,buildings",
                source_external_id=str(building_id),
            )
        finally:
            # Tenant-scoped Mongo reads require a temporary context. Always put
            # the previous context back so a failed bootstrap cannot leak the
            # target building into later work in the same async execution path.
            set_ctx_building_id(previous_building_id)
