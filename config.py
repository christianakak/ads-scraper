from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Claude / Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Supabase
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_key: str = Field(default="", alias="SUPABASE_SERVICE_KEY")

    # Browserless (managed Chromium)
    browserless_token: str = Field(default="", alias="BROWSERLESS_TOKEN")

    # Free APIs
    google_pagespeed_api_key: str = Field(default="", alias="GOOGLE_PAGESPEED_API_KEY")
    meta_ad_library_token: str = Field(default="", alias="META_AD_LIBRARY_TOKEN")

    # Runtime
    env: str = Field(default="dev", alias="ENV")
    audit_cache_ttl_days: int = Field(default=30, alias="AUDIT_CACHE_TTL_DAYS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def is_production(self) -> bool:
        return self.env == "prod"
