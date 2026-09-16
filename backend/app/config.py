import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# The two secrets that ship with placeholder values. They are fine for a
# throwaway dev database and nothing else: anyone who can read this file
# can mint tokens and decrypt every stored device password. Outside
# development the app refuses to start with either still in place.
INSECURE_JWT_SECRET = "change-me-in-production"
INSECURE_ENCRYPTION_KEY = "8w1r6Bt3z5r4h9m2fJmYtq6h0b1c9F8y3nQeR2sT4uI="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Packrat"
    environment: str = "development"

    # Database - defaults to a local sqlite file so the app runs with zero setup.
    # docker-compose points this at Postgres for a production-like stack.
    database_url: str = "sqlite+aiosqlite:///./configcollector.db"

    # Auth
    jwt_secret_key: str = INSECURE_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    # Fernet key used to encrypt device credentials at rest.
    # Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credential_encryption_key: str = INSECURE_ENCRYPTION_KEY
    # Comma-separated keys this install used before (key rotation). Secrets
    # still encrypted with one of them keep decrypting, and the API
    # re-encrypts them with the current key at start-up
    # (services/secret_rekey.py). The shipped placeholder key is always
    # treated as a previous key, so moving off it needs no configuration.
    credential_encryption_previous_keys: str = ""

    # Login brute-force protection (core/login_guard.py): after
    # login_max_failures failed attempts from one IP or against one account
    # within login_window_minutes, further attempts get 429 for a lockout
    # that doubles each time, from login_lockout_seconds up to
    # login_lockout_max_minutes.
    login_max_failures: int = 5
    login_window_minutes: int = 15
    login_lockout_seconds: int = 30
    login_lockout_max_minutes: int = 15

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_task_always_eager: bool = False

    # Netmiko connection defaults
    device_connect_timeout: int = 20

    # Stuck-job cleanup (see services/job_reaper.py). A job whose worker was
    # killed mid-device would otherwise show "running" forever and block
    # deleting its devices. On worker start (and API start in eager mode)
    # every unfinished job is marked interrupted; beat also sweeps every
    # few minutes for jobs with no sign of life for stale_job_minutes.
    # Set reap_jobs_on_start=false when several workers share one broker,
    # since a restarting worker can't know what its siblings are running.
    reap_jobs_on_start: bool = True
    stale_job_minutes: int = 45

    # File logging (see core/logging_config.py). Each process - API,
    # worker, beat - writes packrat-<process>.log under log_dir, rotating
    # at log_max_bytes and keeping log_backup_count older files. LOG_LEVEL
    # DEBUG adds successful GETs, SQL and SSH handshake detail.
    log_dir: str = "./logs"
    log_level: str = "INFO"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    # Admins can view/download the log files from Settings > Troubleshooting.
    # The files cover the whole server (every organisation on it), so a
    # hosted multi-tenant deployment should turn this off and hand logs
    # out from the server itself instead.
    log_download_enabled: bool = True

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


def insecure_secrets(settings: Settings) -> list[str]:
    """Names of the secrets still at their shipped placeholder values."""
    found = []
    if settings.jwt_secret_key == INSECURE_JWT_SECRET or len(settings.jwt_secret_key) < 16:
        found.append("JWT_SECRET_KEY")
    if settings.credential_encryption_key == INSECURE_ENCRYPTION_KEY:
        found.append("CREDENTIAL_ENCRYPTION_KEY")
    return found


def assert_secrets_configured(settings: Settings | None = None) -> None:
    """Refuses to run outside development with placeholder secrets. In
    development it warns instead, so a fresh clone still starts."""
    settings = settings or get_settings()
    missing = insecure_secrets(settings)
    if not missing:
        return
    how = (
        "Set them in backend/.env (or the environment). Generate values with:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(48))\"                       # JWT_SECRET_KEY\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"  # CREDENTIAL_ENCRYPTION_KEY"
    )
    if settings.environment.lower() in ("development", "dev", "test", "testing"):
        logging.getLogger("app").warning(
            "Using placeholder %s - fine for development only. %s", " and ".join(missing), how.replace("\n", " ")
        )
        return
    raise RuntimeError(
        f"Refusing to start with placeholder {' and '.join(missing)} while ENVIRONMENT={settings.environment!r}. "
        f"Anyone with the source code could forge logins or decrypt stored device passwords.\n{how}"
    )
