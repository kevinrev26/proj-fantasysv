import os

import structlog
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

logger = structlog.get_logger()

load_dotenv()

_INSECURE_SECRET_KEY_PLACEHOLDER = "change_this_in_production_please"


class Settings(BaseSettings):
    # Required
    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str

    # Optional with defaults
    ENVIRONMENT: str = "dev"
    ALLOWED_ORIGINS: str = "http://localhost,http://localhost:80,http://127.0.0.1"
    BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    SENTRY_DSN: str | None = None
    LOG_LEVEL: str = "INFO"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 21600
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    DEFAULT_ADMIN_EMAIL: str = "admin@fantasysv.com"
    DEFAULT_ADMIN_PASSWORD: str = "admin"

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS (comma-separated) into a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


def get_settings() -> Settings:
    try:
        logger.info("Loading application settings")
        settings = Settings()  # Pydantic will validate and raise if missing
        logger.info("Settings loaded successfully")

        # Startup security check: reject insecure SECRET_KEY in production.
        if settings.ENVIRONMENT == "prod":
            if settings.SECRET_KEY == _INSECURE_SECRET_KEY_PLACEHOLDER:
                raise RuntimeError(
                    "SECRET_KEY is set to the insecure placeholder value. "
                    'Generate a strong key with: python -c "import secrets; print(secrets.token_hex(32))" '
                    "and set it in your production environment before starting."
                )
        elif settings.SECRET_KEY == _INSECURE_SECRET_KEY_PLACEHOLDER:
            logger.warning(
                "SECRET_KEY is using the insecure placeholder value — "
                "set a strong random key before deploying to production."
            )

        return settings
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Configuration error", error=str(e))
        raise RuntimeError(
            f"Configuration error: {e}\n"
            "Make sure all required env vars are set (see .env.example)"
        )


settings = get_settings()
