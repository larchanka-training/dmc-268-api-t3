import uuid

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Connection

from alembic import command
from app.models import Base

INITIAL = "fb6eae330c65"
INGESTION = "a3c1e5d97b42"


def _seed_legacy_rows(connection: Connection) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("repo", "mr", "job", "ctx")}
    connection.execute(
        text("INSERT INTO repositories (id, external_id, name, url) VALUES (:id, 'r1', 'n', 'u')"),
        {"id": ids["repo"]},
    )
    connection.execute(
        text(
            "INSERT INTO merge_requests (id, repository_id, external_id, title, source_branch,"
            " target_branch, author) VALUES (:id, :repo, '7', 't', 's', 'm', 'a')"
        ),
        {"id": ids["mr"], "repo": ids["repo"]},
    )
    connection.execute(
        text("INSERT INTO review_jobs (id, merge_request_id, status) VALUES (:id, :mr, 'PENDING')"),
        {"id": ids["job"], "mr": ids["mr"]},
    )
    connection.execute(
        text(
            "INSERT INTO context_payloads (id, review_job_id, diff_text)"
            " VALUES (:id, :job, 'diff --git')"
        ),
        {"id": ids["ctx"], "job": ids["job"]},
    )
    connection.commit()
    return ids


def _assert_legacy_rows_intact(connection: Connection, ids: dict[str, uuid.UUID]) -> None:
    row = connection.execute(
        text("SELECT diff_text FROM context_payloads WHERE id = :id"), {"id": ids["ctx"]}
    ).one()
    assert row.diff_text == "diff --git"
    assert (
        connection.execute(
            text("SELECT status FROM review_jobs WHERE id = :id"), {"id": ids["job"]}
        ).scalar_one()
        == "PENDING"
    )


def test_upgrade_downgrade_upgrade_with_existing_rows(
    pg_engine: Engine, alembic_config: Config
) -> None:
    connection: Connection = alembic_config.attributes["connection"]

    command.upgrade(alembic_config, INITIAL)
    connection.commit()
    ids = _seed_legacy_rows(connection)

    command.upgrade(alembic_config, "head")
    connection.commit()
    _assert_legacy_rows_intact(connection, ids)
    new_cols = connection.execute(
        text(
            "SELECT j.head_sha, j.base_sha, c.structured_context, c.provider_metadata,"
            " c.schema_version, c.head_sha AS c_head, c.base_sha AS c_base"
            " FROM review_jobs j JOIN context_payloads c ON c.review_job_id = j.id"
        )
    ).one()
    assert all(value is None for value in new_cols)
    assert "vcs_deliveries" in inspect(connection).get_table_names()

    command.downgrade(alembic_config, "-1")
    connection.commit()
    assert "vcs_deliveries" not in inspect(connection).get_table_names()
    assert "head_sha" not in {c["name"] for c in inspect(connection).get_columns("review_jobs")}
    _assert_legacy_rows_intact(connection, ids)

    command.upgrade(alembic_config, "head")
    connection.commit()
    _assert_legacy_rows_intact(connection, ids)
    assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
        INGESTION
    )


def test_orm_metadata_matches_migrations(pg_engine: Engine, alembic_config: Config) -> None:
    connection: Connection = alembic_config.attributes["connection"]
    command.upgrade(alembic_config, "head")
    connection.commit()

    context = MigrationContext.configure(connection)
    assert compare_metadata(context, Base.metadata) == []
