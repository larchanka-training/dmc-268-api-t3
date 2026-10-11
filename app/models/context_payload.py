import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.review_job import ReviewJob

# JSONB on PostgreSQL; plain JSON elsewhere (SQLite in model tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class ContextPayload(Base):
    __tablename__ = "context_payloads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    review_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    diff_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structured_context: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
    )

    review_job: Mapped[ReviewJob] = relationship(back_populates="context_payload")
