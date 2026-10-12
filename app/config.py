import os
from dataclasses import dataclass, field
from urllib.parse import quote


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    ollama_host: str
    eurorouter_base_url: str
    eurorouter_api_keys: list[str]
    eurorouter_model: str
    redis_url: str
    github_api_base_url: str
    github_token: str
    gitlab_api_base_url: str
    gitlab_token: str
    vcs_timeout_seconds: float
    vcs_max_diff_tokens: int
    recovery_interval_seconds: float
    recovery_stale_after_seconds: float
    github_webhook_secret: str = field(default="", repr=False)
    gitlab_webhook_auth: str = "signed"
    gitlab_webhook_signing_token: str = field(default="", repr=False)
    gitlab_webhook_secret_token: str = field(default="", repr=False)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{quote(self.postgres_user, safe='')}:"
            f"{quote(self.postgres_password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def load_settings() -> Settings:
    return Settings(
        postgres_db=os.getenv("POSTGRES_DB", "app"),
        postgres_user=os.getenv("POSTGRES_USER", "app"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "app"),
        postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        ollama_host=os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434"),
        eurorouter_base_url=os.getenv("EUROROUTER_BASE_URL", ""),
        eurorouter_api_keys=[
            key.strip() for key in os.getenv("EUROROUTER_API_KEYS", "").split(",") if key.strip()
        ],
        eurorouter_model=os.getenv("EUROROUTER_MODEL", "gpt-4o-mini"),
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        github_api_base_url=os.getenv("GITHUB_API_BASE_URL", "https://api.github.com"),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        gitlab_api_base_url=os.getenv("GITLAB_API_BASE_URL", "https://gitlab.com/api/v4"),
        gitlab_token=os.getenv("GITLAB_TOKEN", ""),
        vcs_timeout_seconds=float(os.getenv("VCS_TIMEOUT_SECONDS", "30")),
        vcs_max_diff_tokens=int(os.getenv("VCS_MAX_DIFF_TOKENS", "32000")),
        recovery_interval_seconds=float(os.getenv("RECOVERY_INTERVAL_SECONDS", "30")),
        recovery_stale_after_seconds=float(os.getenv("RECOVERY_STALE_AFTER_SECONDS", "300")),
        github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        gitlab_webhook_auth=os.getenv("GITLAB_WEBHOOK_AUTH", "signed"),
        gitlab_webhook_signing_token=os.getenv("GITLAB_WEBHOOK_SIGNING_TOKEN", ""),
        gitlab_webhook_secret_token=os.getenv("GITLAB_WEBHOOK_SECRET_TOKEN", ""),
    )


settings = load_settings()
