from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    log_level: str = "INFO"

    postgres_user: str = "nice"
    postgres_password: str = "nice"
    postgres_db: str = "nice"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    neo4j_user: str = "neo4j"
    neo4j_password: str = Field(default="nice-poc-2026")
    neo4j_host: str = "localhost"
    neo4j_bolt_port: int = 7687
    neo4j_database: str = "neo4j"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_bolt_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
