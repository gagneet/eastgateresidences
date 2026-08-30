# @featuretrace:owner_read_cutover — PostgreSQL owner read models for building-scoped owner cutover.
# Layer: service
# Data flow: owner_service.py / proposals.py → owner_read_service.py → core.lots + core.parties
#            + core.ownership_periods + core.users (Postgres, building-scoped).
# Related: backend/services/shadow_read_validator.py
#          backend/services/cutover_config_service.py
# Scope: (building-scoped)
"""Postgres-backed owner read service for the owner cutover slice.

Contract boundaries:
1. All public methods are scoped to a single ``building_id`` and never cross schemes.
2. The service answers ownership facts, not general identity or role questions.
3. Other roles held by the same person (ec_member, building_admin, strata_manager)
   are out of scope for this service and deliberately excluded from the DTOs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from db_postgres.repos import config_repo
from db_postgres.session import async_session_context, set_tenant


def _decimal_or_default(value: Any, default: str = "0") -> Decimal:
    """Generated function header.

    Function: _decimal_or_default
    Path: backend/services/owner_read_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


@dataclass(frozen=True)
class OwnerDTO:
    building_id: str
    party_id: str
    user_id: str | None
    email: str | None
    full_name: str
    is_primary_owner: bool
    ownership_share: Decimal
    lot_id: str
    lot_number: str
    unit_number: str
    valid_from: date
    valid_to: date | None

    def to_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: OwnerDTO.to_dict
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            **asdict(self),
            "ownership_share": str(self.ownership_share),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


@dataclass(frozen=True)
class LotDTO:
    building_id: str
    lot_id: str
    lot_number: str
    unit_number: str
    entitlement_units: Decimal

    def to_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: LotDTO.to_dict
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            **asdict(self),
            "entitlement_units": str(self.entitlement_units),
        }


@dataclass(frozen=True)
class OwnerVoterDTO:
    building_id: str
    lot_id: str
    lot_number: str
    unit_number: str
    party_id: str
    user_id: str | None
    email: str | None
    full_name: str
    is_primary_owner: bool
    ownership_share: Decimal
    entitlement_units: Decimal
    valid_from: date
    valid_to: date | None

    def to_dict(self) -> dict[str, Any]:
        """Generated function header.

        Function: OwnerVoterDTO.to_dict
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            **asdict(self),
            "ownership_share": str(self.ownership_share),
            "entitlement_units": str(self.entitlement_units),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


class OwnerReadService:
    """Query building-scoped owner data from Postgres."""

    async def _resolve_scheme(self, building_id: str) -> dict[str, Any]:
        """Generated function header.

        Function: OwnerReadService._resolve_scheme
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await config_repo.resolve_scheme_context(building_id)
        if scheme is None:
            raise RuntimeError(f"No Postgres scheme found for building_id={building_id!r}.")
        return scheme

    async def _fetch_owner_rows(
        self,
        *,
        building_id: str,
        as_at_date: date | None = None,
        user_id: str | None = None,
        lot_identifier: str | None = None,
    ) -> list[OwnerDTO]:
        """Generated function header.

        Function: OwnerReadService._fetch_owner_rows
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        params: dict[str, Any] = {
            "scheme_id": str(scheme["scheme_id"]),
            "as_at_date": as_at_date or date.today(),
        }
        filters: list[str] = []
        if user_id:
            params["user_id"] = user_id
            # Ownership is held by the party — multiple users can belong to one
            # party (e.g. owner + EC role). The lateral picks the *display* user
            # (approved, oldest); EXISTS is the real ownership predicate so a
            # valid owner is never missed when the requested user isn't the
            # lateral's preferred pick.
            filters.append(
                "AND EXISTS ("
                "SELECT 1 FROM core.users uf "
                "WHERE uf.party_id = p.party_id "
                "AND uf.is_active = true "
                "AND uf.user_id::text = :user_id"
                ")"
            )
        if lot_identifier:
            params["lot_identifier"] = lot_identifier
            filters.append(
                "AND (l.lot_id::text = :lot_identifier OR l.lot_number = :lot_identifier OR l.unit_number = :lot_identifier)"
            )

        # When a specific user_id is requested, prefer that user row for the
        # DTO's user_id. Email display still follows core.parties.primary_email
        # first, because that column now represents the owner's chosen contact
        # preference while core.users.email remains the login identity.
        user_lateral_order = (
            "ORDER BY (u.user_id::text = :user_id) DESC, "
            "COALESCE(u.is_approved, false) DESC, u.created_at ASC"
            if user_id
            else "ORDER BY COALESCE(u.is_approved, false) DESC, u.created_at ASC"
        )

        query = f"""
            SELECT l.lot_id::text AS lot_id,
                   l.lot_number,
                   COALESCE(l.unit_number, l.lot_number) AS unit_number,
                   op.owner_party_id::text AS party_id,
                   p.legal_name AS full_name,
                   selected_user.user_id::text AS user_id,
                   COALESCE(p.primary_email, selected_user.email, p.secondary_email, p.metadata->>'email') AS email,
                   op.valid_from,
                   op.valid_to,
                   op.is_primary_owner,
                   COALESCE(op.ownership_share, 1.0) AS ownership_share
            FROM core.ownership_periods op
            JOIN core.lots l
              ON l.lot_id = op.lot_id
            JOIN core.parties p
              ON p.party_id = op.owner_party_id
            LEFT JOIN LATERAL (
                SELECT u.user_id, u.email
                FROM core.users u
                WHERE u.party_id = p.party_id
                  AND u.is_active = true
                {user_lateral_order}
                LIMIT 1
            ) AS selected_user ON true
            WHERE op.scheme_id = :scheme_id
              AND op.recorded_to IS NULL
              AND op.valid_from <= :as_at_date
              AND (op.valid_to IS NULL OR op.valid_to > :as_at_date)
              {' '.join(filters)}
            ORDER BY COALESCE(l.unit_number, l.lot_number),
                     l.lot_number,
                     op.is_primary_owner DESC,
                     op.ownership_share DESC,
                     LOWER(COALESCE(p.primary_email, selected_user.email, p.secondary_email, p.metadata->>'email', p.legal_name))
        """

        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(text(query), params)
            rows = result.fetchall()

        return [
            OwnerDTO(
                building_id=building_id,
                party_id=row.party_id,
                user_id=row.user_id,
                email=row.email,
                full_name=row.full_name,
                is_primary_owner=bool(row.is_primary_owner),
                ownership_share=_decimal_or_default(row.ownership_share, "1.0"),
                lot_id=row.lot_id,
                lot_number=row.lot_number,
                unit_number=row.unit_number,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
            )
            for row in rows
        ]

    async def list_owners_for_scheme(self, building_id: str) -> list[OwnerDTO]:
        """Generated function header.

        Function: OwnerReadService.list_owners_for_scheme
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await self._fetch_owner_rows(building_id=building_id)

    async def get_owner_by_user_id(self, building_id: str, user_id: str) -> OwnerDTO | None:
        """Generated function header.

        Function: OwnerReadService.get_owner_by_user_id
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        rows = await self._fetch_owner_rows(building_id=building_id, user_id=user_id)
        return rows[0] if rows else None

    async def list_owners_by_lot(self, building_id: str, lot_identifier: str) -> list[OwnerDTO]:
        """Generated function header.

        Function: OwnerReadService.list_owners_by_lot
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await self._fetch_owner_rows(building_id=building_id, lot_identifier=lot_identifier)

    async def list_lots_owned_by_user(self, building_id: str, user_id: str) -> list[LotDTO]:
        """Generated function header.

        Function: OwnerReadService.list_lots_owned_by_user
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT l.lot_id::text AS lot_id,
                           l.lot_number,
                           COALESCE(l.unit_number, l.lot_number) AS unit_number,
                           COALESCE(l.entitlement_units, 0) AS entitlement_units
                    FROM core.ownership_periods op
                    JOIN core.lots l
                      ON l.lot_id = op.lot_id
                    JOIN core.users u
                      ON u.party_id = op.owner_party_id
                    WHERE op.scheme_id = :scheme_id
                      AND op.recorded_to IS NULL
                      AND op.valid_from <= CURRENT_DATE
                      AND (op.valid_to IS NULL OR op.valid_to > CURRENT_DATE)
                      AND u.user_id::text = :user_id
                    ORDER BY COALESCE(l.unit_number, l.lot_number), l.lot_number
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "user_id": user_id,
                },
            )
            rows = result.fetchall()

        return [
            LotDTO(
                building_id=building_id,
                lot_id=row.lot_id,
                lot_number=row.lot_number,
                unit_number=row.unit_number,
                entitlement_units=_decimal_or_default(row.entitlement_units),
            )
            for row in rows
        ]

    async def resolve_levy_notice_recipients(self, building_id: str) -> list[OwnerDTO]:
        """Generated function header.

        Function: OwnerReadService.resolve_levy_notice_recipients
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        owners = await self.list_owners_for_scheme(building_id)
        recipients: list[OwnerDTO] = []
        by_lot: dict[str, list[OwnerDTO]] = {}
        for owner in owners:
            by_lot.setdefault(owner.lot_id, []).append(owner)

        for lot_owners in by_lot.values():
            ranked = sorted(
                lot_owners,
                key=lambda owner: (
                    0 if owner.is_primary_owner else 1,
                    -owner.ownership_share,
                    (owner.email or "").lower(),
                    owner.full_name.lower(),
                ),
            )
            recipient = next((owner for owner in ranked if owner.email), ranked[0])
            recipients.append(recipient)

        return sorted(
            recipients,
            key=lambda owner: (owner.unit_number, owner.lot_number, owner.full_name.lower()),
        )

    async def is_user_current_owner_of_unit(
        self,
        building_id: str,
        user_id: str,
        unit_identifier: str,
        as_at_date: date | None = None,
    ) -> bool:
        """Existence check: does ``user_id`` currently own ``unit_identifier``?

        Targeted alternative to ``resolve_agm_voters`` for vote eligibility and
        any other single-(user,unit) BOLA gate. Single Postgres query bounded by
        ``LIMIT 1`` — does not scan the full voter list for the building.
        """
        scheme = await self._resolve_scheme(building_id)
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            row = await session.execute(
                text(
                    """
                    SELECT 1
                    FROM core.ownership_periods op
                    JOIN core.lots l
                      ON l.lot_id = op.lot_id
                    JOIN core.users u
                      ON u.party_id = op.owner_party_id
                    WHERE op.scheme_id = :scheme_id
                      AND op.recorded_to IS NULL
                      AND op.valid_from <= :as_at_date
                      AND (op.valid_to IS NULL OR op.valid_to > :as_at_date)
                      AND u.is_active = true
                      AND u.user_id::text = :user_id
                      AND (
                            l.lot_id::text = :unit_identifier
                         OR l.lot_number = :unit_identifier
                         OR l.unit_number = :unit_identifier
                      )
                    LIMIT 1
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "as_at_date": as_at_date or date.today(),
                    "user_id": user_id,
                    "unit_identifier": unit_identifier,
                },
            )
            return row.first() is not None

    async def resolve_agm_voters(
        self,
        building_id: str,
        as_at_date: date,
    ) -> list[OwnerVoterDTO]:
        """Generated function header.

        Function: OwnerReadService.resolve_agm_voters
        Path: backend/services/owner_read_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme = await self._resolve_scheme(building_id)
        async with async_session_context() as session:
            await set_tenant(session, scheme["tenant_id"])
            result = await session.execute(
                text(
                    """
                    SELECT l.lot_id::text AS lot_id,
                           l.lot_number,
                           COALESCE(l.unit_number, l.lot_number) AS unit_number,
                           op.owner_party_id::text AS party_id,
                           p.legal_name AS full_name,
                           selected_user.user_id::text AS user_id,
                           COALESCE(p.primary_email, selected_user.email, p.secondary_email, p.metadata->>'email') AS email,
                           op.valid_from,
                           op.valid_to,
                           op.is_primary_owner,
                           COALESCE(op.ownership_share, 1.0) AS ownership_share,
                           COALESCE(l.entitlement_units, 0) AS entitlement_units
                    FROM core.ownership_periods op
                    JOIN core.lots l
                      ON l.lot_id = op.lot_id
                    JOIN core.parties p
                      ON p.party_id = op.owner_party_id
                    LEFT JOIN LATERAL (
                        SELECT u.user_id, u.email
                        FROM core.users u
                        WHERE u.party_id = p.party_id
                          AND u.is_active = true
                        ORDER BY COALESCE(u.is_approved, false) DESC, u.created_at ASC
                        LIMIT 1
                    ) AS selected_user ON true
                    WHERE op.scheme_id = :scheme_id
                      AND op.recorded_to IS NULL
                      AND op.valid_from <= :as_at_date
                      AND (op.valid_to IS NULL OR op.valid_to > :as_at_date)
                    ORDER BY COALESCE(l.unit_number, l.lot_number),
                             l.lot_number,
                             op.is_primary_owner DESC,
                             op.ownership_share DESC,
                             LOWER(COALESCE(p.primary_email, selected_user.email, p.secondary_email, p.metadata->>'email', p.legal_name))
                    """
                ),
                {
                    "scheme_id": str(scheme["scheme_id"]),
                    "as_at_date": as_at_date,
                },
            )
            rows = result.fetchall()

        return [
            OwnerVoterDTO(
                building_id=building_id,
                lot_id=row.lot_id,
                lot_number=row.lot_number,
                unit_number=row.unit_number,
                party_id=row.party_id,
                user_id=row.user_id,
                email=row.email,
                full_name=row.full_name,
                is_primary_owner=bool(row.is_primary_owner),
                ownership_share=_decimal_or_default(row.ownership_share, "1.0"),
                entitlement_units=_decimal_or_default(row.entitlement_units),
                valid_from=row.valid_from,
                valid_to=row.valid_to,
            )
            for row in rows
        ]
