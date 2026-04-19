import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # Required
    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str

    # Optional with defaults
    ENVIRONMENT: str = "dev"
    BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"

def get_settings() -> Settings:
    try:
        return Settings()  # Pydantic will validate and raise if missing
    except Exception as e:
        raise RuntimeError(f"Configuration error: {e}\n"
                           "Make sure all required env vars are set (see .env.example)")

settings = get_settings()
