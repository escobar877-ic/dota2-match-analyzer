from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/dota_analyzer"
DOCKER_DATABASE_URL = "postgresql+psycopg://postgres:postgres@postgres:5432/dota_analyzer"


def normalize_database_url_for_local_dev(value: str | None, *, in_docker: bool | None = None) -> str:
    if in_docker is None:
        in_docker = Path("/.dockerenv").exists()
    if not value:
        return DOCKER_DATABASE_URL if in_docker else LOCAL_DATABASE_URL
    if not in_docker and "@postgres:" in value:
        return value.replace("@postgres:", "@localhost:")
    return value


class Settings(BaseSettings):
    database_url: str = DOCKER_DATABASE_URL
    use_demo_data: bool = False
    opendota_api_key: str = ""
    stratz_api_key: str = ""
    pandascore_api_key: str = ""

    @field_validator("database_url", mode="after")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        return normalize_database_url_for_local_dev(value)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
