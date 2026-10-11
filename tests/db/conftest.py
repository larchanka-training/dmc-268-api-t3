import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def pg_engine() -> Iterator[Engine]:
    """Engine for an empty PostgreSQL database selected by TEST_DATABASE_URL.

    The `public` schema is wiped before and after each test: these tests own the
    target database, so point TEST_DATABASE_URL at a throwaway one.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL tests need a throwaway database")
    engine = create_engine(url)
    _reset_schema(engine)
    try:
        yield engine
    finally:
        _reset_schema(engine)
        engine.dispose()


def _reset_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture()
def alembic_config(pg_engine: Engine) -> Iterator[Config]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with pg_engine.connect() as connection:
        config.attributes["connection"] = connection
        yield config
        connection.rollback()
