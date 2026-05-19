"""add analyst_profile phase-2 columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE investment_style_enum AS ENUM ('growth', 'value', 'blend')")
    op.execute(
        "CREATE TYPE output_format_enum AS ENUM ('concise', 'detailed', 'bullet_points')"
    )

    op.add_column(
        "analyst_profiles",
        sa.Column("tracked_tickers", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        "analyst_profiles",
        sa.Column(
            "investment_style",
            postgresql.ENUM(name="investment_style_enum", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "analyst_profiles",
        sa.Column(
            "preferred_output_format",
            postgresql.ENUM(name="output_format_enum", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "analyst_profiles",
        sa.Column("custom_context", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analyst_profiles", "custom_context")
    op.drop_column("analyst_profiles", "preferred_output_format")
    op.drop_column("analyst_profiles", "investment_style")
    op.drop_column("analyst_profiles", "tracked_tickers")

    op.execute("DROP TYPE IF EXISTS output_format_enum")
    op.execute("DROP TYPE IF EXISTS investment_style_enum")
