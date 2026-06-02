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

    # Adyntel (primary ad intelligence — adyntel.com)
    adyntel_api_key: str = Field(default="", alias="ADYNTEL_API_KEY")
    adyntel_email: str = Field(default="", alias="ADYNTEL_EMAIL")
    # False in prod — without a key, collector skips cleanly. True only for local POC.
    adyntel_dummy_mode: bool = Field(default=False, alias="ADYNTEL_DUMMY_MODE")

    # Site Scanner + Portal Quality — Browserless.io (managed Chromium, paid)
    # True: use fixture data when no token (better than empty). False: skip cleanly.
    site_scanner_dummy_mode: bool = Field(default=True, alias="SITE_SCANNER_DUMMY_MODE")
    use_local_browser: bool = Field(default=False, alias="USE_LOCAL_BROWSER")

    # Planning Intel — planning.data.gov.uk (free, no auth required)
    # False: always attempt live. True: fixture only (for offline testing).
    planning_dummy_mode: bool = Field(default=False, alias="PLANNING_DUMMY_MODE")

    # Social/Review — Trustpilot scrape (free) + Google Places (needs GOOGLE_API_KEY)
    # False: always attempt live. True: fixture only (for offline testing).
    reviews_dummy_mode: bool = Field(default=False, alias="REVIEWS_DUMMY_MODE")

    # Google APIs — one key, enable PageSpeed Insights + Places APIs on same GCP project
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    # Legacy aliases — accepted for backwards compat
    google_pagespeed_api_key: str = Field(default="", alias="GOOGLE_PAGESPEED_API_KEY")

    # Legacy — kept as fallback only
    meta_ad_library_token: str = Field(default="", alias="META_AD_LIBRARY_TOKEN")

    # Runtime
    env: str = Field(default="dev", alias="ENV")
    audit_cache_ttl_days: int = Field(default=30, alias="AUDIT_CACHE_TTL_DAYS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def is_production(self) -> bool:
        return self.env == "prod"
