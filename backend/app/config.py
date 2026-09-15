from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Packrat"
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

    # Firmware push: uploaded image files are stored on local disk (not
    # the DB - these can be hundreds of MB, far past what's sane for a text
    # column) under this directory, one file per FirmwareImage row.
    firmware_storage_dir: str = "./firmware_images"
    # TFTP/FTP's own well-known ports (69, 21) are privileged (<1024) on
    # both Linux and Windows - binding them needs the process to run
    # elevated. Defaulted here to those standard ports since most vendor
    # "copy tftp:"/"copy ftp:" implementations assume them and can't be told
    # to use another one; SCP's port is freely choosable on the device side
    # (given as part of the scp:// URL), so it defaults to a plain,
    # non-privileged port instead.
    firmware_tftp_port: int = 69
    firmware_ftp_port: int = 21
    firmware_scp_port: int = 2222
    # How long a push job's transfer server stays up waiting for a target
    # device to finish pulling the file (and how long the device's copy
    # command is given to return to its prompt), before it's torn down
    # regardless - a safety bound so a stuck/never-connecting device doesn't
    # leave a file server (and the firmware image it's serving) exposed
    # indefinitely.
    firmware_transfer_timeout_seconds: int = 60 * 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
