"""add chunk_count to documents

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("chunk_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "chunk_count")