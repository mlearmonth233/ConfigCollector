"""Standalone entrypoint for the ephemeral SCP server that firmware pushes
rely on (see services/scp_server.py for the actual implementation and its
security model). Runs as its own long-lived process/container - separate
from the FastAPI app and the Celery worker - since it needs to keep a
listening socket open independent of any request or task lifecycle, and
needs no HTTP or Celery machinery of its own. It talks to the same Redis
instance the worker mints grants into (see services/scp_grants.py) as its
only shared state.

Run with: python -m app.scp_server_main
"""

from app.config import get_settings
from app.services.scp_server import serve_forever


def main() -> None:
    settings = get_settings()
    serve_forever(
        bind_host=settings.scp_server_bind_host,
        port=settings.scp_server_port,
        host_key_path=settings.scp_server_host_key_path,
    )


if __name__ == "__main__":
    main()
