"""Ensure HNSW index and pgvector extension for cosine similarity search

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-24

Guarantees the HNSW index used by the <=> cosine distance operator in
retrieval.py exists. 0006 also creates an HNSW index (ix_document_chunks_embedding)
but under a different name and without CONCURRENTLY. This migration is
idempotent (IF NOT EXISTS) and safe on all deployment environments.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension — no-op if already present
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    # CONCURRENTLY cannot run inside a transaction block; autocommit_block
    # commits the current transaction, runs the statement in autocommit mode,
    # then begins a new transaction.
    with op.get_context().autocommit_block():
        op.execute(sa.text("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_document_chunks_embedding_hnsw
            ON document_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw"))
