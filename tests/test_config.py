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


def test_vcs_settings_defaults(monkeypatch):
    for name in (
        "GITHUB_API_BASE_URL",
        "GITHUB_TOKEN",
        "VCS_TIMEOUT_SECONDS",
        "VCS_MAX_DIFF_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.github_api_base_url == "https://api.github.com"
    assert settings.github_token == ""
    assert settings.vcs_timeout_seconds == 30.0
    assert settings.vcs_max_diff_tokens == 32000


def test_vcs_settings_read_env(monkeypatch):
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://ghe.example.com/api/v3")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("VCS_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("VCS_MAX_DIFF_TOKENS", "1000")

    settings = load_settings()

    assert settings.github_api_base_url == "https://ghe.example.com/api/v3"
    assert settings.github_token == "tok"
    assert settings.vcs_timeout_seconds == 5.5
    assert settings.vcs_max_diff_tokens == 1000
