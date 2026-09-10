import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    ollama_host: str

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
    )


settings = load_settings()
