"""vcs ingestion: deliveries, revisions, structured context

Revision ID: a3c1e5d97b42
Revises: fb6eae330c65
Create Date: 2026-10-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c1e5d97b42"
down_revision: str | Sequence[str] | None = "fb6eae330c65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "vcs_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("delivery_id", sa.String(length=255), nullable=False),
        sa.Column("repository_external_id", sa.String(length=255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("base_sha", sa.String(length=64), nullable=True),
        sa.Column("repository_name", sa.String(length=255), nullable=True),
        sa.Column("repository_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("source_branch", sa.String(length=255), nullable=True),
        sa.Column("target_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "SUCCEEDED",
                "FAILED",
                name="vcsdeliverystatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_vcs_deliveries_provider_delivery"),
        sa.UniqueConstraint(
            "provider",
            "repository_external_id",
            "pr_number",
            "head_sha",
            name="uq_vcs_deliveries_revision",
        ),
    )
    op.create_index(
        "ix_vcs_deliveries_status_next_attempt_at",
        "vcs_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_merge_requests_repository_external_id",
        "merge_requests",
        ["repository_id", "external_id"],
    )

    op.add_column("review_jobs", sa.Column("head_sha", sa.String(length=64), nullable=True))
    op.add_column("review_jobs", sa.Column("base_sha", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_review_jobs_merge_request_head", "review_jobs", ["merge_request_id", "head_sha"]
    )

    op.add_column(
        "context_payloads",
        sa.Column(
            "structured_context",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "context_payloads",
        sa.Column(
            "provider_metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column("context_payloads", sa.Column("schema_version", sa.Integer(), nullable=True))
    op.add_column("context_payloads", sa.Column("head_sha", sa.String(length=64), nullable=True))
    op.add_column("context_payloads", sa.Column("base_sha", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("context_payloads", "base_sha")
    op.drop_column("context_payloads", "head_sha")
    op.drop_column("context_payloads", "schema_version")
    op.drop_column("context_payloads", "provider_metadata")
    op.drop_column("context_payloads", "structured_context")

    op.drop_constraint("uq_review_jobs_merge_request_head", "review_jobs", type_="unique")
    op.drop_column("review_jobs", "base_sha")
    op.drop_column("review_jobs", "head_sha")

    op.drop_constraint("uq_merge_requests_repository_external_id", "merge_requests", type_="unique")

    op.drop_index("ix_vcs_deliveries_status_next_attempt_at", table_name="vcs_deliveries")
    op.drop_table("vcs_deliveries")
