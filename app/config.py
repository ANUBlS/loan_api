from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS = {"change-me", "change-me-too", "change-me-admin", ""}


class Settings(BaseSettings):
    """All settings come from environment variables or the .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Smart Mobile Loan API"
    app_version: str = "1.0.0"
    environment: str = "development"  # development | production

    database_url: str = "postgresql+psycopg://loan:loan@localhost:5432/loan_api"

    # JWT access tokens
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # One-time SMS codes
    otp_secret: str = "change-me-too"
    otp_length: int = 6
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 60
    otp_max_per_hour: int = 5
    # Returns the code in the API response so you can test without an SMS
    # gateway. Never enable in production.
    expose_otp_in_response: bool = True

    # Back-office endpoints (/api/v1/admin/*) require header X-Admin-Key
    admin_api_key: str = "change-me-admin"

    # Business
    timezone: str = "Asia/Baku"
    currency: str = "AZN"
    max_pending_applications: int = 3

    cors_origins: list[str] = ["*"]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.is_production:
            weak = [
                name
                for name in ("jwt_secret", "otp_secret", "admin_api_key")
                if getattr(self, name) in _INSECURE_DEFAULTS
                or len(getattr(self, name)) < 32
            ]
            if weak:
                raise ValueError(
                    "Set strong values (32+ chars) for: " + ", ".join(weak)
                )
            if self.expose_otp_in_response:
                raise ValueError(
                    "EXPOSE_OTP_IN_RESPONSE must be false in production"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
