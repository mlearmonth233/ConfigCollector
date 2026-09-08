from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "ConfigCollector"
    environment: str = "development"

    # Database - defaults to a local sqlite file so the app runs with zero setup.
    # docker-compose points this at Postgres for a production-like stack.
    database_url: str = "sqlite+aiosqlite:///./configcollector.db"

    # Auth
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    # Fernet key used to encrypt device credentials at rest.
    # Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credential_encryption_key: str = "8w1r6Bt3z5r4h9m2fJmYtq6h0b1c9F8y3nQeR2sT4uI="

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_task_always_eager: bool = False

    # Netmiko connection defaults
    device_connect_timeout: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
