import os
from dataclasses import dataclass


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

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
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
    )


settings = load_settings()
