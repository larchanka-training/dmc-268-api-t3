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
        "GITLAB_API_BASE_URL",
        "GITLAB_TOKEN",
        "VCS_TIMEOUT_SECONDS",
        "VCS_MAX_DIFF_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.github_api_base_url == "https://api.github.com"
    assert settings.github_token == ""
    assert settings.gitlab_api_base_url == "https://gitlab.com/api/v4"
    assert settings.gitlab_token == ""
    assert settings.vcs_timeout_seconds == 30.0
    assert settings.vcs_max_diff_tokens == 32000


def test_vcs_settings_read_env(monkeypatch):
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://ghe.example.com/api/v3")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITLAB_API_BASE_URL", "https://gl.example.com/api/v4")
    monkeypatch.setenv("GITLAB_TOKEN", "gltok")
    monkeypatch.setenv("VCS_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("VCS_MAX_DIFF_TOKENS", "1000")

    settings = load_settings()

    assert settings.github_api_base_url == "https://ghe.example.com/api/v3"
    assert settings.github_token == "tok"
    assert settings.gitlab_api_base_url == "https://gl.example.com/api/v4"
    assert settings.gitlab_token == "gltok"
    assert settings.vcs_timeout_seconds == 5.5
    assert settings.vcs_max_diff_tokens == 1000


def test_webhook_settings_defaults(monkeypatch):
    for name in (
        "GITHUB_WEBHOOK_SECRET",
        "GITLAB_WEBHOOK_AUTH",
        "GITLAB_WEBHOOK_SIGNING_TOKEN",
        "GITLAB_WEBHOOK_SECRET_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.github_webhook_secret == ""
    assert settings.gitlab_webhook_auth == "signed"
    assert settings.gitlab_webhook_signing_token == ""
    assert settings.gitlab_webhook_secret_token == ""


def test_webhook_settings_read_env_and_hide_secrets_in_repr(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "gh-secret-value")
    monkeypatch.setenv("GITLAB_WEBHOOK_AUTH", "token")
    monkeypatch.setenv("GITLAB_WEBHOOK_SIGNING_TOKEN", "whsec_signing-value")
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET_TOKEN", "gl-token-value")

    settings = load_settings()

    assert settings.github_webhook_secret == "gh-secret-value"
    assert settings.gitlab_webhook_auth == "token"
    assert settings.gitlab_webhook_signing_token == "whsec_signing-value"
    assert settings.gitlab_webhook_secret_token == "gl-token-value"
    text = repr(settings)
    for secret in ("gh-secret-value", "whsec_signing-value", "gl-token-value"):
        assert secret not in text


def test_settings_repr_hides_secret_values(monkeypatch):
    secrets = {
        "GITHUB_TOKEN": "gh-token-value",
        "GITLAB_TOKEN": "gl-token-value",
        "POSTGRES_PASSWORD": "pg-password-value",
        "EUROROUTER_API_KEYS": "key-one-value,key-two-value",
        "GITHUB_WEBHOOK_SECRET": "gh-webhook-value",
        "GITLAB_WEBHOOK_SIGNING_TOKEN": "gl-signing-value",
        "GITLAB_WEBHOOK_SECRET_TOKEN": "gl-secret-value",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    rendered = repr(load_settings())

    for value in (
        "gh-token-value",
        "gl-token-value",
        "pg-password-value",
        "key-one-value",
        "key-two-value",
        "gh-webhook-value",
        "gl-signing-value",
        "gl-secret-value",
    ):
        assert value not in rendered
