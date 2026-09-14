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

    # Firmware push: uploaded images are stored on local disk under this
    # directory (one subfolder per org) - see services/firmware_storage.py.
    firmware_storage_path: str = "./firmware_storage"

    # Ephemeral SCP server (services/scp_server.py) that devices connect to
    # during a firmware push - see app/scp_server_main.py for the standalone
    # process that runs it.
    #
    # Address/port the server process itself binds to.
    scp_server_bind_host: str = "0.0.0.0"
    scp_server_port: int = 2222
    # Hostname/IP devices should use to reach the server above - this is
    # baked into the "copy scp://..." command sent to each device, so it
    # must be routable from the managed devices' network, not just from
    # wherever this backend/worker happens to run. No safe default exists;
    # firmware pushes fail fast with a clear error until this is set.
    scp_server_public_host: str = ""
    # Where the server's SSH host key is persisted (generated on first run
    # if missing) so it presents the same identity across restarts instead
    # of a fresh one devices have never seen. Lives in its own directory
    # (rather than directly in the working directory) so docker-compose can
    # mount just that directory as a volume.
    scp_server_host_key_path: str = "./data/scp_host_key"
    # How long a per-device-item SCP grant (one-time username/password,
    # scoped to exactly one file) stays valid before it's rejected even if
    # unused - generous enough for a slow firmware transfer over a WAN link,
    # short enough that a grant a device never claims doesn't linger.
    scp_grant_ttl_seconds: int = 1800


@lru_cache
def get_settings() -> Settings:
    return Settings()
