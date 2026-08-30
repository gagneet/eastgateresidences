# @featuretrace:documents — PostgreSQL read + write for the documents domain.
# Layer: service
# Data flow: server.py /documents routes → services/documents_store.py → this repo
#            → documents.documents (building-scoped) via RLS tenant context.
# Related: backend/services/documents_store.py (the dispatcher that calls this)
#          backend/services/store_router.py (decides whether this repo is used)
#          backend/utils/test_data_flag.py (is_test_data backstop)
# Table: documents.documents (read + write)
# Tests: tests/backend/test_documents_repo.py
"""Typed Postgres access for `documents.documents`.

This is the first repository in the codebase to carry a real Postgres WRITE path for
a non-finance domain. Until 2026-08-29 every mutating route in the application wrote
MongoDB only: `finance_route_cutover_service`'s 37 policies were all `read_only=True`
and its docstring said so outright — *"Write routes must remain Mongo-primary in this
phase."*

Two rules this file exists to hold:

**Tenant context is mandatory, not defensive.** `documents.documents` carries a strict
`tenant_id = core.current_tenant_id()` RLS policy with NO bypass clause. A query issued
without `set_tenant` returns zero rows and raises nothing — indistinguishable from "this
building has no documents". Every function here sets it, and `_require_tenant` refuses
to run rather than return a silent empty result.

**`is_test_data` is set by the writer, not the caller.** A test exercising a production
handler reaches this module through the real `DATABASE_URL` with no test double
(footgun #20), so the flag is ORed with `under_pytest()` here rather than trusted from
above.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from utils.test_data_flag import under_pytest

logger = logging.getLogger(__name__)

# documents.documents.folder_id and uploader_user_id are UUID columns; the MongoDB
# equivalents are arbitrary strings. Checked before binding so a legacy value produces
# a clean refusal rather than a mid-query cast error.
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Cached per process: the column either exists in this deployment or it does not, and
# re-checking information_schema on every read would add a round-trip to every request.
# None = not yet checked.
_IS_PUBLIC_COLUMN: bool | None = None


class TenantContextMissing(RuntimeError):
    """Raised instead of silently returning zero rows under RLS."""


class VisibilityNotExpressible(RuntimeError):
    """The caller's visibility predicate cannot be represented against this schema.

    Raised rather than approximated. Both approximations are wrong in a way that is
    invisible at the call site: a broader predicate discloses documents, a narrower one
    hides them, and neither announces itself. The caller falls back to MongoDB.
    """


async def _has_is_public_column() -> bool:
    """Is Alembic 0105 applied in this deployment?

    Deliberately probed rather than assumed. A repository that assumes its own
    migration has run produces a confusing runtime error on a database that is one
    revision behind — which happens routinely between a deploy's migrate and restart
    steps, and on any developer machine that has not run `alembic upgrade head`.
    """
    global _IS_PUBLIC_COLUMN
    if _IS_PUBLIC_COLUMN is not None:
        return _IS_PUBLIC_COLUMN
    async with async_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'documents'
                       AND table_name = 'documents'
                       AND column_name = 'is_public'
                ) AS present
                """
            )
        )
        _IS_PUBLIC_COLUMN = bool(result.scalar())
    if not _IS_PUBLIC_COLUMN:
        logger.warning(
            "documents.documents.is_public is absent (Alembic 0105 not applied) — "
            "Postgres cannot express the document visibility predicate; reads fall back to MongoDB"
        )
    return _IS_PUBLIC_COLUMN


async def _require_tenant(building_id: str) -> dict[str, str]:
    """Resolve tenant + scheme, or refuse.

    Never derive a tenant_id from a building_id — it is resolved from `core.schemes`
    (canonical_owners.yaml: postgres-tenant-resolution). Registration once derived it
    with uuid5 and every insert was rejected by the FK while a try/except swallowed it.
    """
    scheme = await resolve_scheme_context(building_id)
    if not scheme or not scheme.get("tenant_id") or not scheme.get("scheme_id"):
        raise TenantContextMissing(
            f"no Postgres scheme context for building_id={building_id!r} — "
            f"refusing to query documents.documents without tenant context, because "
            f"RLS would return zero rows and no error"
        )
    return {"tenant_id": str(scheme["tenant_id"]), "scheme_id": str(scheme["scheme_id"])}


def _row_to_document(row: Any) -> dict[str, Any]:
    """Shape a Postgres row into the dict `server.py`'s `DocumentResponse` accepts.

    The field names here are NOT the column names, deliberately. `DocumentResponse`
    requires `id`, `title` and **`category`**, and defaults the rest; a shape that
    returned `original_filename`/`mime_type` verbatim would validate as a document with
    no filename and no type, and one missing `category` would raise
    ResponseValidationError the moment this domain is promoted — a 500 that could only
    appear in production, after a routing change, on a path no test exercises while the
    domain is still Mongo-primary.

    `category` is carried in `tags[0]` because `documents.documents` has no category
    column and the Mongo writer puts the category there on write. When a document
    predates that convention it reads as "general" rather than failing — a missing
    category is a display detail, not a reason to refuse the row (the same judgement
    `DocumentResponse`'s own docstring records about the 240 legacy rows).
    """
    tags = list(row.tags or [])
    created = row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else str(row.created_at or "")
    updated = row.updated_at.isoformat() if hasattr(row.updated_at, "isoformat") else str(row.updated_at or "")
    return {
        "id": str(row.document_id),
        "building_id": None,  # filled by the caller, which knows the legacy identifier
        "title": row.title,
        "category": tags[0] if tags else "general",
        "file_name": row.original_filename,
        "file_type": row.mime_type,
        "file_size": int(row.file_size_bytes or 0),
        "file_data": None,  # bytes live in Mongo; Postgres holds the record
        "folder_id": str(row.folder_id) if row.folder_id else None,
        "allowed_roles": list(row.allowed_roles or []),
        "tags": tags,
        "retention_class": row.retention_class,
        "is_archived": bool(row.is_archived),
        "uploaded_by": str(row.uploader_user_id) if row.uploader_user_id else "",
        "created_at": created,
        "updated_at": updated,
        "storage_key": row.storage_key,
        "source_store": "postgres",
    }


@dataclass(frozen=True)
class DocumentVisibility:
    """Who is asking, expressed so the SQL predicate can be built from it.

    There is deliberately NO default and no "None means everything" mode. The first
    version of this repository took `allowed_roles: list[str] | None = None` and
    treated None as "no filter"; `server.py` never passed the argument, so a
    Postgres-served read would have returned EVERY document in the building to ANY
    caller — `GET /documents` uses `get_optional_user`, so that includes callers with
    no session at all. It was inert only because the domain is not promoted, which is
    exactly how a defect of this shape reaches production: it appears for the first
    time immediately after a routing change, on a path no test covers while the domain
    is still Mongo-primary.

    Making the context a required construction argument means a caller cannot get an
    unfiltered read by omission. To read everything you must say `unrestricted=True`
    and mean it.
    """

    viewer_roles: tuple[str, ...] = ()
    viewer_user_id: str | None = None
    include_public: bool = True
    unrestricted: bool = False

    @classmethod
    def for_roles(
        cls,
        roles: list[str] | tuple[str, ...] | None,
        *,
        viewer_user_id: str | None = None,
        include_public: bool = True,
    ) -> "DocumentVisibility":
        return cls(
            viewer_roles=tuple(roles or ()),
            viewer_user_id=viewer_user_id,
            include_public=include_public,
        )

    @classmethod
    def unrestricted_read(cls) -> "DocumentVisibility":
        """Every document in the scheme. For operators and parity checks, not routes."""
        return cls(unrestricted=True)


async def list_documents(
    building_id: str,
    *,
    visibility: DocumentVisibility,
    folder_id: str | None = None,
    include_archived: bool = False,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return documents for one building from Postgres, filtered to what `visibility` allows.

    An empty list is a real answer here — the table is genuinely empty for every
    building today — so the CALLER decides whether to fall back, not this function.
    Returning None to mean "nothing found" would collapse "empty" and "unavailable"
    into one value, which is the distinction the whole cutover depends on.

    Raises `VisibilityNotExpressible` when the predicate cannot be represented against
    the current schema, rather than silently degrading to a broader or narrower read.
    """
    ctx = await _require_tenant(building_id)

    # Every entry in `clauses` is a CONSTANT string chosen by this function; no caller
    # value is ever interpolated. Values reach the query only as bound parameters, so
    # the f-string below is safe despite looking like the thing that never is. Same
    # convention as financial_read_service's `grace_predicate`.
    #
    # tenant_id is filtered here AND enforced by RLS. That is deliberate belt-and-braces:
    # the predicate makes the intent reviewable in the SQL, and RLS makes it true even if
    # someone edits the predicate away.
    clauses = ["d.tenant_id = CAST(:tenant_id AS UUID)", "d.scheme_id = CAST(:scheme_id AS UUID)"]
    params: dict[str, Any] = {**ctx, "limit": int(limit)}

    if not include_archived:
        clauses.append("d.is_archived = FALSE")
    if folder_id is not None:
        if folder_id == "":
            clauses.append("d.folder_id IS NULL")
        else:
            # A legacy Mongo folder_id is an arbitrary string, not a UUID. Reject it
            # here so the caller falls back to Mongo, rather than letting Postgres
            # raise a cast error mid-query and be read as "Postgres is down".
            if not _UUID_RE.match(folder_id):
                raise VisibilityNotExpressible(
                    f"folder_id {folder_id!r} is not a UUID; documents.documents.folder_id "
                    f"is a UUID column and this is a legacy MongoDB folder identifier"
                )
            clauses.append("d.folder_id = CAST(:folder_id AS UUID)")
            params["folder_id"] = folder_id

    if not visibility.unrestricted:
        # Mirror of MongoDB's three-branch predicate:
        #     is_public OR uploaded_by == me OR allowed_roles CONTAINS my_effective_role
        # Each branch is added only when this caller actually has it, and the branches
        # are OR-ed with each other but AND-ed with the tenant/folder/archive clauses.
        branches: list[str] = []
        if visibility.include_public:
            if not await _has_is_public_column():
                # Alembic 0105 adds this column. Without it the predicate cannot be
                # expressed, and BOTH ways of approximating it are wrong: dropping the
                # public branch hides public documents, keeping it open returns them
                # all. Refuse, so the caller falls back to MongoDB.
                raise VisibilityNotExpressible(
                    "documents.documents has no is_public column (Alembic 0105 adds it); "
                    "the MongoDB visibility predicate cannot be expressed in Postgres"
                )
            branches.append("d.is_public = TRUE")
        if visibility.viewer_user_id:
            # Best-effort, and only when the id is actually a UUID: a MongoDB user id
            # is not a core.users UUID (footgun #24 — the same person has different
            # ids in the two stores), so a non-UUID viewer id simply contributes no
            # branch rather than matching nothing loudly.
            if _UUID_RE.match(visibility.viewer_user_id):
                branches.append("d.uploader_user_id = CAST(:viewer_user_id AS UUID)")
                params["viewer_user_id"] = visibility.viewer_user_id
        if visibility.viewer_roles:
            # Overlap, not containment: visible when ANY of the caller's roles appears.
            branches.append("d.allowed_roles && CAST(:roles AS TEXT[])")
            params["roles"] = list(visibility.viewer_roles)

        if not branches:
            # A restricted caller with no expressible branch can see nothing. Return
            # that explicitly instead of running a query whose WHERE clause would omit
            # the visibility filter entirely.
            return []
        clauses.append("(" + " OR ".join(branches) + ")")

    async with async_session_context() as session:
        await set_tenant(session, ctx["tenant_id"])
        result = await session.execute(
            text(
                f"""
                SELECT d.document_id, d.title, d.original_filename, d.mime_type,
                       d.storage_key, d.file_size_bytes, d.folder_id, d.allowed_roles,
                       d.tags, d.retention_class, d.is_archived, d.uploader_user_id,
                       d.created_at, d.updated_at
                  FROM documents.documents d
                 WHERE {' AND '.join(clauses)}
                 ORDER BY d.created_at DESC
                 LIMIT :limit
                """
            ),
            params,
        )
        rows = result.fetchall()

    docs = [_row_to_document(r) for r in rows]
    for doc in docs:
        doc["building_id"] = building_id
    return docs


async def create_document(
    building_id: str,
    *,
    title: str,
    original_filename: str,
    mime_type: str,
    storage_key: str,
    file_size_bytes: int | None = None,
    content_hash: str | None = None,
    folder_id: str | None = None,
    allowed_roles: list[str] | None = None,
    tags: list[str] | None = None,
    retention_class: str = "seven_year",
    uploader_user_id: str | None = None,
    is_public: bool = False,
    is_test_data: bool = False,
) -> dict[str, Any]:
    """Insert one document into Postgres and return it. Commits before returning.

    The commit boundary matters for the mirror: `documents_store` replays the MongoDB
    write only AFTER this returns, never inside the open transaction, so a rollback
    here cannot leave MongoDB holding a document Postgres does not have (footgun #21).
    """
    ctx = await _require_tenant(building_id)

    # The column is written only when this deployment has it (Alembic 0105). Naming it
    # unconditionally would make every write fail on a database one revision behind,
    # which is the normal state between a deploy's migrate and restart steps. Both
    # fragments below are literals chosen here, never caller input.
    has_public = await _has_is_public_column()
    is_public_col = "is_public," if has_public else ""
    is_public_val = ":is_public," if has_public else ""

    async with async_session_context() as session:
        await set_tenant(session, ctx["tenant_id"])
        result = await session.execute(
            text(
                f"""
                INSERT INTO documents.documents (
                    tenant_id, scheme_id, folder_id, title, original_filename,
                    mime_type, storage_key, file_size_bytes, content_hash,
                    uploader_user_id, retention_class, allowed_roles, tags,
                    {is_public_col} is_test_data
                ) VALUES (
                    CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                    CAST(:folder_id AS UUID), :title, :original_filename,
                    :mime_type, :storage_key, :file_size_bytes, :content_hash,
                    CAST(:uploader_user_id AS UUID), :retention_class,
                    CAST(:allowed_roles AS TEXT[]), CAST(:tags AS TEXT[]),
                    {is_public_val} :is_test_data
                )
                RETURNING document_id, title, original_filename, mime_type, storage_key,
                          file_size_bytes, folder_id, allowed_roles, tags, retention_class,
                          is_archived, uploader_user_id, created_at, updated_at
                """
            ),
            {
                **ctx,
                "folder_id": folder_id or None,
                "title": title,
                "original_filename": original_filename,
                "mime_type": mime_type,
                "storage_key": storage_key,
                "file_size_bytes": int(file_size_bytes) if file_size_bytes is not None else None,
                "content_hash": content_hash,
                "uploader_user_id": uploader_user_id or None,
                "retention_class": retention_class,
                "allowed_roles": list(allowed_roles or []),
                "tags": list(tags or []),
                # Set here, never trusted from the caller — see the module docstring.
                "is_test_data": bool(is_test_data) or under_pytest(),
                **({"is_public": bool(is_public)} if has_public else {}),
            },
        )
        row = result.fetchone()
        await session.commit()

    doc = _row_to_document(row)
    doc["building_id"] = building_id
    return doc


async def archive_document(building_id: str, document_id: str, *, archived_by: str | None = None) -> bool:
    """Soft-archive one document. Returns True when a row actually changed.

    Never a hard DELETE: 7-year retention applies to every record in this domain.
    The boolean is the post-condition — "no exception" is not "changed a row"
    (footgun #24).
    """
    ctx = await _require_tenant(building_id)

    async with async_session_context() as session:
        await set_tenant(session, ctx["tenant_id"])
        result = await session.execute(
            text(
                """
                UPDATE documents.documents
                   SET is_archived = TRUE,
                       archived_at = now(),
                       archived_by = CAST(:archived_by AS UUID),
                       updated_at = now()
                 WHERE document_id = CAST(:document_id AS UUID)
                   AND tenant_id = CAST(:tenant_id AS UUID)
                   AND is_archived = FALSE
                """
            ),
            {**ctx, "document_id": document_id, "archived_by": archived_by or None},
        )
        await session.commit()
        return int(result.rowcount or 0) > 0


async def count_documents(building_id: str, *, include_archived: bool = False) -> int:
    """Row count for this building. Used by the DR parity check, not by routes."""
    ctx = await _require_tenant(building_id)
    async with async_session_context() as session:
        await set_tenant(session, ctx["tenant_id"])
        result = await session.execute(
            text(
                f"""
                SELECT count(*) AS n FROM documents.documents
                 WHERE tenant_id = CAST(:tenant_id AS UUID)
                   AND scheme_id = CAST(:scheme_id AS UUID)
                   {'' if include_archived else 'AND is_archived = FALSE'}
                """
            ),
            ctx,
        )
        return int(result.scalar() or 0)
