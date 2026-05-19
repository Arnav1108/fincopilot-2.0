"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Extensions ────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── 2. Enum types ────────────────────────────────────────────────────────
    op.execute("CREATE TYPE message_role AS ENUM ('user', 'assistant')")
    op.execute(
        "CREATE TYPE document_type AS ENUM "
        "('10-K', '10-Q', 'transcript', 'presentation', 'research_note', 'other')"
    )
    op.execute(
        "CREATE TYPE document_status AS ENUM ('pending', 'processing', 'ready', 'failed')"
    )

    # ── 3. users ─────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("clerk_user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
    )

    # ── 4. analyst_profiles ──────────────────────────────────────────────────
    op.create_table(
        "analyst_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preferred_name", sa.Text(), nullable=True),
        sa.Column("firm", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("sectors_of_interest", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("preferred_output_length", sa.Text(), nullable=True),
        sa.Column("preferred_citation_style", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_analyst_profiles_user_id"),
    )

    # ── 5. conversations ─────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("rolling_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    # ── 6. messages ──────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="message_role", create_type=False),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("agent_trace", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # ── 7. documents ─────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("ticker", sa.Text(), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="document_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_user_id_status", "documents", ["user_id", "status"])

    # ── 8. document_chunks ───────────────────────────────────────────────────
    # Raw SQL: op.create_table cannot express the vector(1536) column type.
    op.execute("""
        CREATE TABLE document_chunks (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            user_id     UUID        NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            chunk_index INTEGER     NOT NULL,
            content     TEXT        NOT NULL,
            embedding   vector(1536),
            metadata    JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.create_index(
        "ix_document_chunks_user_id_document_id",
        "document_chunks",
        ["user_id", "document_id"],
    )
    op.execute("""
        CREATE INDEX ix_document_chunks_embedding
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # ── 9. eval_runs ─────────────────────────────────────────────────────────
    op.create_table(
        "eval_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("faithfulness_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("answer_relevancy_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("context_recall_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("context_precision_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("total_pairs", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_eval_runs_run_date", "eval_runs", ["run_date"])


def downgrade() -> None:
    # Tables drop their indexes automatically; drop in reverse dependency order.
    op.drop_table("eval_runs")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("analyst_profiles")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS document_status")
    op.execute("DROP TYPE IF EXISTS document_type")
    op.execute("DROP TYPE IF EXISTS message_role")

    op.execute("DROP EXTENSION IF EXISTS vector")
