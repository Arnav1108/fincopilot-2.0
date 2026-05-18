import uuid
from datetime import date, datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    faithfulness_score: Mapped[Optional[float]] = mapped_column(sa.Numeric(5, 4), nullable=True)
    answer_relevancy_score: Mapped[Optional[float]] = mapped_column(sa.Numeric(5, 4), nullable=True)
    context_recall_score: Mapped[Optional[float]] = mapped_column(sa.Numeric(5, 4), nullable=True)
    context_precision_score: Mapped[Optional[float]] = mapped_column(sa.Numeric(5, 4), nullable=True)
    total_pairs: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
