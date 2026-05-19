import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clerk_user_id: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    profile: Mapped[Optional["AnalystProfile"]] = relationship("AnalystProfile", back_populates="user", uselist=False)
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="user")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="user")


class AnalystProfile(Base):
    __tablename__ = "analyst_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    preferred_name: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    firm: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    role: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    sectors_of_interest: Mapped[Optional[list]] = mapped_column(ARRAY(sa.Text()), nullable=True)
    tracked_tickers: Mapped[Optional[list]] = mapped_column(ARRAY(sa.Text()), nullable=True)
    investment_style: Mapped[Optional[str]] = mapped_column(
        ENUM("growth", "value", "blend", name="investment_style_enum", create_type=False), nullable=True
    )
    preferred_output_format: Mapped[Optional[str]] = mapped_column(
        ENUM("concise", "detailed", "bullet_points", name="output_format_enum", create_type=False), nullable=True
    )
    custom_context: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    preferred_output_length: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    preferred_citation_style: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    user: Mapped["User"] = relationship("User", back_populates="profile")
