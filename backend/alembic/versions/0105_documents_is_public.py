"""0105 — documents.documents gains is_public, so the visibility predicate is expressible.

Revision ID: 0105_documents_is_public
Revises: 0104_manager_fn_scoping
Create Date: 2026-08-29

# @featuretrace:documents — the column that lets Postgres answer "who may see this".
# Layer: migration
# Data flow: documents.documents.is_public → db_postgres/repos/documents_repo.list_documents
#            → services/documents_store.read_documents → GET /documents (building-scoped).
# Related: backend/db_postgres/repos/documents_repo.py
#          backend/services/documents_store.py
#          backend/server.py (GET /documents)
# Table: documents.documents

Adds `is_public BOOLEAN NOT NULL DEFAULT FALSE`.

WHY THIS IS A CORRECTNESS FIX, NOT A FEATURE
--------------------------------------------
MongoDB's `GET /documents` filters visibility with a three-branch predicate:

    is_public  OR  uploaded_by == me  OR  allowed_roles CONTAINS my_effective_role

`documents.documents` carried `allowed_roles` and `uploader_user_id` but had **no
is_public equivalent** — verified against the live catalog 2026-08-29. So the
predicate could not be expressed in Postgres at all, and a Postgres-served read was
guaranteed to be wrong in one of two directions:

* omit the role filter and it OVER-serves — every document in the building returned
  to any caller, including an unauthenticated one, because `GET /documents` uses
  `get_optional_user`; or
* apply only the role filter and it UNDER-serves — every public document disappears
  for a caller whose role is not explicitly listed.

The first of those is what the initial version of `documents_repo.list_documents`
would have done: `server.py` never passed `allowed_roles`, the parameter defaulted to
`None`, and `None` meant "no filter". It was inert only because the `documents`
domain is not promoted, which is precisely the shape of defect that surfaces for the
first time in production, immediately after a routing change, on a path no test
covers while the domain is still Mongo-primary.

DEFAULT FALSE IS THE SAFE DIRECTION
-----------------------------------
Defaulting to FALSE means an existing row is treated as NOT public. For a visibility
flag the safe default is the restrictive one: a document wrongly marked private is
invisible to someone who should have seen it, which is a support request; a document
wrongly marked public is disclosed to everyone in the building, which is not
recoverable. The table is empty for every building today, so no row is actually
affected — but the default has to be right for the backfill that follows, and for any
row an importer writes without setting it.

Additive, defaulted, reversible. No backfill, no row changes.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0105_documents_is_public"
down_revision = "0104_manager_fn_scoping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the visibility flag, defaulted to the restrictive value."""
    op.add_column(
        "documents",
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "TRUE when the document is visible to every member of the scheme, "
                "mirroring MongoDB documents.is_public. Defaults FALSE: for a "
                "visibility flag the safe default is the restrictive one."
            ),
        ),
        schema="documents",
    )


def downgrade() -> None:
    """Drop the flag.

    Postgres then cannot express the visibility predicate again, so
    `documents_repo.list_documents` must refuse to serve rather than fall back to an
    unfiltered read — see its `_VISIBILITY_REQUIRES_IS_PUBLIC` guard.
    """
    op.drop_column("documents", "is_public", schema="documents")
