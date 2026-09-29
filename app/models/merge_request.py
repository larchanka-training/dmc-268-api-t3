import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.repository import Repository

if TYPE_CHECKING:
    from app.models.review_job import ReviewJob


class MergeRequest(Base):
    __tablename__ = "merge_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500))
    source_branch: Mapped[str] = mapped_column(String(255))
    target_branch: Mapped[str] = mapped_column(String(255))
    author: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # pylint: disable=not-callable
    )

    repository: Mapped[Repository] = relationship(back_populates="merge_requests")
    review_jobs: Mapped[list["ReviewJob"]] = relationship(
        back_populates="merge_request", cascade="all, delete-orphan"
    )
