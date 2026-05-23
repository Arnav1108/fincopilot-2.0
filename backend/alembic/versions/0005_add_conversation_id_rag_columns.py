"""add conversation_id to documents/chunks and RAG columns to messages

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23

Backfill strategy for conversation_id
--------------------------------------
conversation_id is added NULLABLE on both documents and document_chunks so
this migration is safe to apply against an existing dataset.  Existing rows
have no conversation to reference.  Before enforcing NOT NULL, choose one:

  Option A — purge orphaned documents (dev / clean-slate environments):
      DELETE FROM document_chunks WHERE conversation_id IS NULL;
      DELETE FROM documents        WHERE conversation_id IS NULL;

  Option B — backfill into a sentinel conversation (staging / prod):
      -- create one "legacy-import" conversation per user, then:
      UPDATE documents        SET conversation_id = <sentinel_id> WHERE conversation_id IS NULL;
      UPDATE document_chunks  SET conversation_id = <sentinel_id> WHERE conversation_id IS NULL;

After backfill, run a follow-up migration (0006) that executes:
    ALTER TABLE documents       ALTER COLUMN conversation_id SET NOT NULL;
    ALTER TABLE document_chunks ALTER COLUMN conversation_id SET NOT NULL;
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. documents.conversation_id ─────────────────────────────────────────
    op.add_column(
        "documents",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,      # becomes NOT NULL after backfill — see module docstring
        ),
    )
    op.create_foreign_key(
        "fk_documents_conversation_id",
        "documents",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ── 2. document_chunks.conversation_id ───────────────────────────────────
    op.add_column(
        "document_chunks",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,      # becomes NOT NULL after backfill — see module docstring
        ),
    )
    op.create_foreign_key(
        "fk_document_chunks_conversation_id",
        "document_chunks",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # Composite index for the primary RAG retrieval query:
    # WHERE conversation_id = $1 AND user_id = $2
    op.create_index(
        "ix_document_chunks_conversation_id_user_id",
        "document_chunks",
        ["conversation_id", "user_id"],
    )

    # ── 3. messages — RAG tracking columns ───────────────────────────────────
    # rag_used: safe as NOT NULL immediately; server_default backfills existing rows to false.
    op.add_column(
        "messages",
        sa.Column(
            "rag_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "messages",
        sa.Column("relevance_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("retrieved_chunk_ids", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    # ── messages ─────────────────────────────────────────────────────────────
    op.drop_column("messages", "retrieved_chunk_ids")
    op.drop_column("messages", "relevance_score")
    op.drop_column("messages", "rag_used")

    # ── document_chunks ──────────────────────────────────────────────────────
    op.drop_index(
        "ix_document_chunks_conversation_id_user_id",
        table_name="document_chunks",
    )
    op.drop_constraint(
        "fk_document_chunks_conversation_id",
        "document_chunks",
        type_="foreignkey",
    )
    op.drop_column("document_chunks", "conversation_id")

    # ── documents ────────────────────────────────────────────────────────────
    op.drop_constraint(
        "fk_documents_conversation_id",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "conversation_id")
