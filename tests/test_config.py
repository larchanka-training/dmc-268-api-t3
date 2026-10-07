from dataclasses import replace

from sqlalchemy.engine import make_url

from app.config import load_settings


def test_database_url_escapes_special_characters_in_credentials():
    settings = replace(
        load_settings(), postgres_user="app@team", postgres_password="p@ss:w/rd% sp+ace"
    )

    url = make_url(settings.database_url)

    assert url.username == "app@team"
    assert url.password == "p@ss:w/rd% sp+ace"
    assert url.host == settings.postgres_host
    assert url.database == settings.postgres_db


def test_redis_url_defaults_to_compose_service(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert load_settings().redis_url == "redis://redis:6379/0"


def test_redis_url_reads_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6380/2")

    assert load_settings().redis_url == "redis://example:6380/2"
