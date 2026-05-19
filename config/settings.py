"""Centralized configuration via pydantic-settings.

All environment variables are loaded and validated here.
Every other module imports `settings` from this module.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Aura application settings loaded from environment variables."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Google Calendar
    google_client_id: str = Field(..., description="OAuth 2.0 Client ID")
    google_client_secret: str = Field(..., description="OAuth 2.0 Client Secret")
    google_refresh_token: str = Field(..., description="OAuth refresh token")
    google_calendar_id: str = Field(default="primary", description="Calendar ID to sync")

    # Gemini AI
    gemini_api_key: str = Field(..., description="Google Gemini API key")

    # Supabase
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_key: str = Field(..., description="Supabase anon/public key")

    # App
    app_env: str = Field(default="development", description="Environment name")
    app_port: int = Field(default=8000, description="Server port")
    day_start_hour: int = Field(default=6, description="Day window start (24h)")
    day_end_hour: int = Field(default=23, description="Day window end (24h)")
    timezone: str = Field(default="America/New_York", description="Local timezone")

    # Web Push (VAPID)
    vapid_public_key: str = Field(default="", description="VAPID public key for push notifications")
    vapid_private_key: str = Field(default="", description="VAPID private key for push notifications")
    vapid_email: str = Field(default="mailto:aura@example.com", description="VAPID contact email")


# Singleton — import `settings` everywhere
settings = Settings()
