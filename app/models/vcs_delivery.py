import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VcsDeliveryStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class VcsDelivery(Base):
    """Durable inbox row for one accepted, normalized VCS webhook delivery."""

    __tablename__ = "vcs_deliveries"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_vcs_deliveries_provider_delivery"),
        UniqueConstraint(
            "provider",
            "repository_external_id",
            "pr_number",
            "head_sha",
            name="uq_vcs_deliveries_revision",
        ),
        Index("ix_vcs_deliveries_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    delivery_id: Mapped[str] = mapped_column(String(255))

    # Normalized event fields.
    repository_external_id: Mapped[str] = mapped_column(String(255))
    pr_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    head_sha: Mapped[str] = mapped_column(String(64))
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repository_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repository_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Processing state.
    status: Mapped[VcsDeliveryStatus] = mapped_column(
        Enum(VcsDeliveryStatus, native_enum=False, validate_strings=True),
        default=VcsDeliveryStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
        onupdate=func.now(),  # pylint: disable=not-callable
    )
