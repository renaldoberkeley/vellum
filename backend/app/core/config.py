from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DATABASE_URL = "postgresql+psycopg://vellum:vellum@localhost:5432/vellum"


class Settings(BaseSettings):
    app_name: str = "Vellum API"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = ""
    secret_db_host: str = ""
    secret_db_name: str = ""
    secret_db_username: str = ""
    secret_db_password: str = ""
    secret_db_port: int = 5432
    cors_origins: str = "http://localhost:3000"
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    ai_max_context_chars: int = 24_000
    ai_max_history_messages: int = 16
    ai_max_output_tokens: int = 700
    ai_edit_max_output_tokens: int = 7_500

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_cors_origins() -> list[str]:
    settings = get_settings()
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]


def get_database_url(settings: Settings | None = None) -> str:
    resolved_settings = settings or get_settings()

    if resolved_settings.database_url.strip():
        return resolved_settings.database_url.strip()

    secret_parts = (
        resolved_settings.secret_db_host,
        resolved_settings.secret_db_name,
        resolved_settings.secret_db_username,
        resolved_settings.secret_db_password,
    )
    if all(part.strip() for part in secret_parts):
        return (
            "postgresql+psycopg://"
            f"{resolved_settings.secret_db_username}:{resolved_settings.secret_db_password}"
            f"@{resolved_settings.secret_db_host}:{resolved_settings.secret_db_port}"
            f"/{resolved_settings.secret_db_name}"
        )

    return DEFAULT_DATABASE_URL
