# @featuretrace:documents — store-agnostic documents access: PG-first read, dual write.
# Layer: service
# Data flow: server.py /documents routes → store_router.resolve_store("documents") →
#            documents_repo (PostgreSQL) and/or db.documents (MongoDB) (building-scoped).
# Related: backend/services/store_router.py (the dispatch decision)
#          backend/db_postgres/repos/documents_repo.py (the Postgres side)
#          backend/workers/scheduler.py::documents_dr_parity_check (the DR check)
# Collection: documents (read + write), documents.documents (read + write)
# Tests: tests/backend/test_documents_store.py
"""The documents domain's single store-agnostic entry point.

This is the pattern the rest of the cutover follows: a route calls this module and
never names a store. Which store serves is decided once, by the control plane, in
`store_router.resolve_store`.

Read behaviour — PG-first with a directional fallback
-----------------------------------------------------
When the control plane says Postgres, Postgres is queried first. MongoDB is used when
Postgres is *unavailable* (an exception, or missing tenant context) — never merely
because Postgres returned nothing, which is a real answer. But `documents.documents`
is empty for every building today while MongoDB holds 82 live documents, so a strict
"empty means empty" read would blank the documents page the moment the domain is
promoted. `read_documents` therefore treats an empty Postgres result as a *coexistence
union* rather than a truth: it falls back to Mongo and says so in `source`.

That union is temporary and explicitly bounded — it exists only while a domain has a
Postgres table and no Postgres data. Once data genesis has run for a domain, the
union is removed and empty means empty. `GET /users` has carried the same shape since
identity_core was promoted, undocumented; naming it here makes it a phase, not a
permanent architecture.

Write behaviour — Postgres first, Mongo mirrored after commit
--------------------------------------------------------------
While both stores are live, MongoDB is what could rebuild Postgres. A Postgres-primary
write that is not mirrored destroys that DR position silently. So a write:

1. commits to Postgres,
2. THEN mirrors into MongoDB — never inside the open transaction, because a rollback
   would otherwise leave Mongo holding a document Postgres never accepted (footgun #21),
3. and reports which halves succeeded, so a partial write is visible rather than
   assumed.

A Mongo mirror failure does NOT fail the request — the authoritative write already
committed — but it is logged loudly and surfaced in the return value, because a silent
mirror failure is exactly how the two stores drift apart.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from services.store_router import resolve_store

if TYPE_CHECKING:  # import only for typing — the repo module is imported lazily below
    from db_postgres.repos.documents_repo import DocumentVisibility

logger = logging.getLogger(__name__)

DOMAIN = "documents"


async def read_documents(
    building_id: str,
    *,
    visibility: "DocumentVisibility",
    folder_id: str | None = None,
    mongo_query: dict[str, Any] | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Return `{"documents": [...], "source": ..., "decision": ...}` for one building.

    `visibility` is REQUIRED and has no default. The Mongo and Postgres paths must
    answer the same question, and the only way to guarantee that is to make the caller
    state who is asking. An earlier version defaulted the role filter to `None` and
    treated `None` as "no filter", which would have returned every document in the
    building to any caller the moment this domain was promoted.

    `mongo_query` is the caller's already-built Mongo filter, passed through untouched
    so the route's permission logic is not re-implemented here — duplicating it would
    create exactly the kind of second implementation the capability index exists to
    prevent. `visibility` is the same policy re-expressed for SQL, not a second source
    of truth: when the two cannot be made to agree, the Postgres path refuses.
    """
    from database import db

    decision = await resolve_store(
        domain=DOMAIN, building_id=building_id, operation="read", route="documents.list",
    )

    pg_docs: list[dict[str, Any]] | None = None
    pg_refused: str | None = None
    if decision.use_postgres:
        try:
            from db_postgres.repos.documents_repo import (
                VisibilityNotExpressible,
                list_documents,
            )

            try:
                pg_docs = await list_documents(
                    building_id,
                    visibility=visibility,
                    folder_id=folder_id,
                    limit=limit,
                )
            except VisibilityNotExpressible as exc:
                # Not an error and not an empty result — Postgres cannot answer THIS
                # question safely. Serve Mongo and say why, rather than serving a
                # broader or narrower set that nothing downstream could detect.
                pg_refused = str(exc)
                logger.info(
                    "documents_store: Postgres cannot express this read for building=%s (%s) "
                    "— serving MongoDB", building_id, exc,
                )
                pg_docs = None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "documents_store: Postgres read failed for building=%s (%s) — falling back to Mongo",
                building_id, exc,
            )
            pg_docs = None

    if pg_docs:
        return {"documents": pg_docs, "source": "postgres", "decision": decision}

    query = dict(mongo_query or {"building_id": building_id})
    mongo_docs = await db.documents.find(query, {"_id": 0, "file_data": 0}).to_list(limit)
    for doc in mongo_docs:
        doc.setdefault("source_store", "mongo")

    if decision.use_postgres:
        # Three distinct reasons we are serving Mongo, kept distinct so an operator
        # reading a log or an API `source` field can tell them apart. Collapsing them
        # would hide the one that matters: a refusal is a schema/policy gap that needs
        # fixing, an empty result is the expected coexistence window, and unavailable
        # is an incident.
        if pg_refused is not None:
            source = "mongo_fallback_pg_cannot_express"
        elif pg_docs is not None:
            source = "mongo_fallback_pg_empty"
        else:
            source = "mongo_fallback_pg_unavailable"
    else:
        source = "mongo"
    return {
        "documents": mongo_docs,
        "source": source,
        "decision": decision,
        "pg_refused_reason": pg_refused,
    }


async def write_document(
    building_id: str,
    *,
    title: str,
    original_filename: str,
    mime_type: str,
    storage_key: str,
    file_size_bytes: int | None = None,
    folder_id: str | None = None,
    allowed_roles: list[str] | None = None,
    tags: list[str] | None = None,
    uploader_user_id: str | None = None,
    is_public: bool = False,
    mongo_document: dict[str, Any] | None = None,
    is_test_data: bool = False,
) -> dict[str, Any]:
    """Create a document in the authoritative store, mirroring into the other.

    Returns `{"document": ..., "source": ..., "postgres_written": bool,
    "mongo_written": bool, "mirror_error": str | None}`.
    """
    from database import db

    decision = await resolve_store(
        domain=DOMAIN, building_id=building_id, operation="write", route="documents.create",
    )

    postgres_written = False
    mongo_written = False
    mirror_error: str | None = None
    document: dict[str, Any] | None = None

    if decision.use_postgres:
        try:
            from db_postgres.repos.documents_repo import create_document

            document = await create_document(
                building_id,
                title=title,
                original_filename=original_filename,
                mime_type=mime_type,
                storage_key=storage_key,
                file_size_bytes=file_size_bytes,
                folder_id=folder_id,
                allowed_roles=allowed_roles,
                tags=tags,
                uploader_user_id=uploader_user_id,
                is_public=is_public,
                is_test_data=is_test_data,
            )
            postgres_written = True
        except Exception as exc:  # noqa: BLE001
            # Directional fallback: Postgres was designated but could not accept the
            # write, so Mongo takes it and the request still succeeds.
            logger.error(
                "documents_store: Postgres write failed for building=%s (%s) — writing Mongo only",
                building_id, exc,
            )
            mirror_error = f"postgres_write_failed: {exc}"

    # The Mongo half. This runs for two DIFFERENT reasons and the distinction decides
    # whether it may be skipped:
    #   * Mongo-primary domain  -> this IS the authoritative write. Never skippable.
    #   * Postgres-primary      -> this is the post-commit DR mirror, gated on
    #                              decision.mirror_to_mongo.
    #
    # `mirror_to_mongo` is the real control, not documentation: it is True for every
    # Postgres write while both stores are live, and goes False only when a domain is
    # decommissioned off MongoDB (Phase 5). Reading it here is what makes that
    # transition a control-plane decision rather than a code change — and what stops
    # this property being the decorative accessor it was when first written.
    mirror_required = (not decision.use_postgres) or decision.mirror_to_mongo
    if not mirror_required:
        logger.debug(
            "documents_store: MongoDB mirror skipped for building=%s — domain is "
            "decommissioned off MongoDB (mirror_to_mongo=False)",
            building_id,
        )
        return {
            "document": document or {},
            "source": "postgres",
            "postgres_written": postgres_written,
            "mongo_written": False,
            "mirror_error": mirror_error,
        }

    mongo_doc = dict(mongo_document or {})
    mongo_doc.setdefault("id", (document or {}).get("id") or str(uuid4()))
    mongo_doc.setdefault("building_id", building_id)
    mongo_doc.setdefault("title", title)
    mongo_doc.setdefault("filename", original_filename)
    mongo_doc.setdefault("file_type", mime_type)
    if is_test_data:
        mongo_doc["is_test_data"] = True
    try:
        await db.documents.update_one(
            {"id": mongo_doc["id"], "building_id": building_id},
            {"$setOnInsert": mongo_doc},
            upsert=True,
        )
        mongo_written = True
    except Exception as exc:  # noqa: BLE001
        # Never fails the request when Postgres already committed — but never silent
        # either: an unmirrored Postgres write is a DR hole, not a cosmetic issue.
        logger.error(
            "documents_store: MongoDB mirror FAILED for building=%s id=%s (%s) — "
            "stores are now divergent for this document",
            building_id, mongo_doc.get("id"), exc,
        )
        mirror_error = mirror_error or f"mongo_mirror_failed: {exc}"
        if not postgres_written:
            raise

    return {
        "document": document or mongo_doc,
        "source": "postgres" if postgres_written else "mongo",
        "postgres_written": postgres_written,
        "mongo_written": mongo_written,
        "mirror_error": mirror_error,
    }


async def measure_documents_parity(building_id: str) -> dict[str, Any]:
    """Compare document counts across both stores. Read-only; for the DR check.

    Deliberately reports counts rather than asserting equality: while the coexistence
    window is open, Postgres being behind Mongo is the EXPECTED state, not a fault.
    What matters is that the number is measured every day instead of discovered later.
    """
    from database import db

    mongo_count = await db.documents.count_documents(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    )
    try:
        from db_postgres.repos.documents_repo import count_documents

        pg_count = await count_documents(building_id)
        pg_available = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("documents parity: Postgres count failed for %s: %s", building_id, exc)
        pg_count, pg_available = 0, False

    return {
        "building_id": building_id,
        "mongo_count": mongo_count,
        "postgres_count": pg_count,
        "postgres_available": pg_available,
        "gap": mongo_count - pg_count,
    }
