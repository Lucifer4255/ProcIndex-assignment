"""Application settings loaded from environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379"
    llm_api_key: str
    llm_model: str = "meta-llama/llama-3.3-70b-instruct"
    logfire_token: str | None = None
    port: int = 8000

    google_service_account_json: str
    google_calendar_id: str
    google_sheet_id: str | None = None

    # Vapi (Phase 2)
    vapi_api_key: str | None = None
    vapi_llm_provider: str = "openai"
    vapi_llm_model: str = "gpt-4o-mini"
    vapi_phone_number_id: str | None = None
    vapi_assistant_id: str | None = None
    base_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
