import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import structlog

logger = structlog.get_logger()

load_dotenv()

class Settings(BaseSettings):
    # Required
    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str

    # Optional with defaults
    ENVIRONMENT: str = "dev"
    BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    SENTRY_DSN: str | None = None
    LOG_LEVEL: str = "INFO"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    DEFAULT_ADMIN_EMAIL: str = "admin@fantasysv.com"
    DEFAULT_ADMIN_PASSWORD: str = "admin"

    class Config:
        env_file = ".env"
        extra = "ignore"

def get_settings() -> Settings:
    try:
        logger.info("Loading application settings")
        settings = Settings()  # Pydantic will validate and raise if missing
        logger.info("Settings loaded successfully")
        return settings
    except Exception as e:
        logger.error("Configuration error", error=str(e))
        raise RuntimeError(f"Configuration error: {e}\n"
                           "Make sure all required env vars are set (see .env.example)")

settings = get_settings()
