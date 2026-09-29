import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.merge_request import MergeRequest

if TYPE_CHECKING:
    from app.models.context_payload import ContextPayload
    from app.models.finding import Finding


class ReviewJobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewJob(Base):
    __tablename__ = "review_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merge_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merge_requests.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ReviewJobStatus] = mapped_column(
        Enum(ReviewJobStatus, native_enum=False, validate_strings=True),
        default=ReviewJobStatus.PENDING,
    )
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    merge_request: Mapped[MergeRequest] = relationship(back_populates="review_jobs")
    context_payload: Mapped["ContextPayload | None"] = relationship(
        back_populates="review_job", uselist=False, cascade="all, delete-orphan"
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="review_job", cascade="all, delete-orphan"
    )
