"""0039 — Documents schema: documents and document_folders tables.

Creates the `documents` schema and its two foundational tables, providing
a PostgreSQL home for document metadata that currently lives only in MongoDB
(`documents` and `document_folders` collections).

  documents.document_folders  — folder hierarchy per scheme (unlimited depth)
  documents.documents         — one row per uploaded artefact: hash, MIME,
                                retention class, soft-delete state, permissions

File blobs continue to live in S3-compatible object storage keyed by
`documents.storage_key`. Thumbnails and OCR extracts stay in MongoDB for
flexible-schema reasons. `documents.documents` is the only writable surface
for document permissions and folder placement.

Retention: ACT Unit Titles (Mgt) Act 2011 ss.115–116 /
NSW Strata Schemes Mgt Act 2015 ss.93–95 — 7-year minimum.
`retention_class` and `retention_deadline` drive the soft-delete enforcement.
Delete = soft-cancel only (is_archived = true). Hard delete is forbidden except
for records with `is_test_data = true`.

The inline `server.py /documents` and `/folders` handlers will fold into
`routers/documents.py` per IDENTITY-PG-CUTOVER-01 once this schema is live.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0039_documents_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS documents")

    # ------------------------------------------------------------------
    # documents.document_folders
    # Folder hierarchy with self-referential parent FK (unlimited depth).
    # Top-level folders have parent_folder_id = NULL.
    # Unique constraint prevents duplicate names within the same parent.
    # ------------------------------------------------------------------
    op.create_table(
        "document_folders",
        sa.Column(
            "folder_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        # NULL means top-level folder; otherwise FK to parent in same table
        sa.Column("parent_folder_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        # general, legal, financial, governance, compliance, maintenance, communications
        sa.Column("folder_type", sa.Text(), nullable=False, server_default=sa.text("'general'")),
        # Roles permitted to view this folder (empty array = all authenticated users)
        sa.Column(
            "allowed_roles",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "archived_by",
            sa.UUID(),
            sa.ForeignKey("core.users.user_id"),
            nullable=True,
        ),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="documents",
    )

    # Self-referential FK added after table creation to avoid circular dependency
    op.create_foreign_key(
        "document_folders_parent_fk",
        "document_folders",
        "document_folders",
        ["parent_folder_id"],
        ["folder_id"],
        source_schema="documents",
        referent_schema="documents",
    )

    # Unique folder names per parent — must use a functional index with COALESCE
    # because PostgreSQL UNIQUE constraints treat each NULL as distinct, so a plain
    # UNIQUE(scheme_id, parent_folder_id, name) would allow duplicate top-level
    # folder names. COALESCE maps NULL to a fixed sentinel UUID so all top-level
    # folders share the same "parent" bucket in the index.
    op.execute(
        """
        CREATE UNIQUE INDEX document_folders_scheme_parent_name_ux
            ON documents.document_folders (
                scheme_id,
                COALESCE(parent_folder_id, '00000000-0000-0000-0000-000000000000'::uuid),
                name
            )
        """
    )

    op.create_index(
        "document_folders_scheme_idx",
        "document_folders",
        ["scheme_id", "parent_folder_id"],
        schema="documents",
    )
    op.create_index(
        "document_folders_tenant_idx",
        "document_folders",
        ["tenant_id"],
        schema="documents",
    )

    # ------------------------------------------------------------------
    # documents.documents
    # One row per uploaded artefact.
    # content_hash enables deduplication across uploads.
    # retention_class and retention_deadline enforce the 7-year rule.
    # ------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column(
            "document_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column(
            "folder_id",
            sa.UUID(),
            sa.ForeignKey("documents.document_folders.folder_id"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        # S3-compatible object key (never exposed directly to clients)
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        # SHA-256 of file content for deduplication and integrity verification
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "uploader_user_id",
            sa.UUID(),
            sa.ForeignKey("core.users.user_id"),
            nullable=True,
        ),
        # permanent, seven_year, three_year, one_year
        # Drives the retention_deadline and soft-delete enforcement.
        sa.Column(
            "retention_class",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'seven_year'"),
        ),
        # Computed at upload time from retention_class + created_at.
        # NULL means "retain indefinitely" (permanent class).
        sa.Column("retention_deadline", sa.Date(), nullable=True),
        # Roles permitted to view this document (empty = same as folder)
        sa.Column(
            "allowed_roles",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        # Soft-delete — hard delete is forbidden for non-test records
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "archived_by",
            sa.UUID(),
            sa.ForeignKey("core.users.user_id"),
            nullable=True,
        ),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "retention_class IN ('permanent', 'seven_year', 'three_year', 'one_year')",
            name="documents_retention_class_chk",
        ),
        schema="documents",
    )

    op.create_index(
        "documents_scheme_folder_idx",
        "documents",
        ["scheme_id", "folder_id"],
        schema="documents",
    )
    op.create_index(
        "documents_scheme_archived_idx",
        "documents",
        ["scheme_id", "is_archived"],
        schema="documents",
    )
    op.create_index(
        "documents_content_hash_idx",
        "documents",
        ["content_hash"],
        schema="documents",
    )
    op.create_index(
        "documents_tenant_idx",
        "documents",
        ["tenant_id"],
        schema="documents",
    )

    # ------------------------------------------------------------------
    # Grants and RLS
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$ BEGIN
            GRANT USAGE ON SCHEMA documents TO strataos_user;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA documents TO strataos_user;
            ALTER DEFAULT PRIVILEGES IN SCHEMA documents
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO strataos_user;
        EXCEPTION WHEN others THEN NULL;
        END $$;
        """
    )

    for table in ["document_folders", "documents"]:
        op.execute(f"ALTER TABLE documents.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE documents.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_documents_{table} ON documents.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0039_documents_schema.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for table in ["documents", "document_folders"]:
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_documents_{table} ON documents.{table}"
        )
        op.execute(f"ALTER TABLE documents.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("documents_tenant_idx", table_name="documents", schema="documents")
    op.drop_index("documents_content_hash_idx", table_name="documents", schema="documents")
    op.drop_index("documents_scheme_archived_idx", table_name="documents", schema="documents")
    op.drop_index("documents_scheme_folder_idx", table_name="documents", schema="documents")
    op.drop_table("documents", schema="documents")

    op.drop_constraint("document_folders_parent_fk", "document_folders", schema="documents", type_="foreignkey")
    op.drop_index("document_folders_tenant_idx", table_name="document_folders", schema="documents")
    op.drop_index("document_folders_scheme_idx", table_name="document_folders", schema="documents")
    op.execute("DROP INDEX IF EXISTS documents.document_folders_scheme_parent_name_ux")
    op.drop_table("document_folders", schema="documents")

    op.execute("DROP SCHEMA IF EXISTS documents")
