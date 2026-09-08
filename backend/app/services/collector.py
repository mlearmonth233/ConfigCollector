"""Blocking Netmiko collection logic. Runs inside a Celery worker process,
never on the FastAPI event loop.
"""

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from app.config import get_settings
from app.services.device_types import get_device_type_spec, resolve_commands

settings = get_settings()


class CollectionError(Exception):
    """Raised when a device could not be reached or its config could not be
    read. Message is safe to store/display - never includes credentials."""


def collect_device_config(
    *,
    host: str,
    port: int,
    device_type: str,
    username: str,
    password: str,
    secret: str | None,
    custom_commands: str | None,
) -> str:
    spec = get_device_type_spec(device_type)
    commands = resolve_commands(device_type, custom_commands)

    connection_params = {
        "device_type": spec.netmiko_driver,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "timeout": settings.device_connect_timeout,
        "conn_timeout": settings.device_connect_timeout,
    }
    if secret and spec.secret_supported:
        connection_params["secret"] = secret

    try:
        with ConnectHandler(**connection_params) as conn:
            if secret and spec.secret_supported:
                conn.enable()
            outputs = []
            for command in commands:
                outputs.append(f"! ---- {command} ----")
                outputs.append(conn.send_command(command, read_timeout=60))
            return "\n".join(outputs)
    except NetmikoAuthenticationException as exc:
        raise CollectionError(f"Authentication failed for {host}:{port}") from exc
    except NetmikoTimeoutException as exc:
        raise CollectionError(f"Timed out connecting to {host}:{port}") from exc
    except Exception as exc:  # noqa: BLE001 - surface as a job failure, not a crash
        raise CollectionError(f"Failed to collect config from {host}:{port}: {exc}") from exc
