"""Fix indexes dropped by 1cfb79155de6 and relax conversation_id nullability

Revision ID: 0006
Revises: 1cfb79155de6
Create Date: 2026-05-24

What this corrects from the auto-generated 1cfb79155de6 migration:
- Restores the HNSW vector-search index on document_chunks.embedding
  (dropped silently by autogenerate; critical for similarity search)
- Restores ix_document_chunks_user_id_document_id and ix_documents_user_id_status
- Restores the partial index on conversations.deleted_at for soft-delete queries
- Replaces single-column ix_document_chunks_conversation_id with the composite
  (conversation_id, user_id) index that the retrieval query actually uses
- Relaxes conversation_id to nullable on both documents and document_chunks so
  existing/legacy rows are safe; enforce NOT NULL in 0007 after backfill
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "1cfb79155de6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Restore conversations soft-delete partial index ────────────────────
    op.create_index(
        "ix_conversations_deleted_at",
        "conversations",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── 2. Restore document_chunks supporting indexes ─────────────────────────
    op.create_index(
        "ix_document_chunks_user_id_document_id",
        "document_chunks",
        ["user_id", "document_id"],
    )

    # Replace single-column index with composite used by RAG retrieval
    op.drop_index("ix_document_chunks_conversation_id", table_name="document_chunks")
    op.create_index(
        "ix_document_chunks_conversation_id_user_id",
        "document_chunks",
        ["conversation_id", "user_id"],
    )

    # Restore HNSW index — must use raw SQL to preserve vector_cosine_ops operator class
    op.execute("""
        CREATE INDEX ix_document_chunks_embedding
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # ── 3. Restore documents supporting index ─────────────────────────────────
    op.create_index(
        "ix_documents_user_id_status",
        "documents",
        ["user_id", "status"],
    )

    # ── 4. Relax conversation_id to nullable (safe backfill path) ─────────────
    op.alter_column(
        "documents",
        "conversation_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "document_chunks",
        "conversation_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Reverse nullability (will fail if NULLs are present)
    op.alter_column(
        "document_chunks",
        "conversation_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "documents",
        "conversation_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("ix_documents_user_id_status", table_name="documents")

    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding")
    op.drop_index("ix_document_chunks_conversation_id_user_id", table_name="document_chunks")
    op.create_index(
        "ix_document_chunks_conversation_id",
        "document_chunks",
        ["conversation_id"],
    )
    op.drop_index("ix_document_chunks_user_id_document_id", table_name="document_chunks")

    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
