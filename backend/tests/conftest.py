import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_db():
    await init_db()
    yield
    os.remove(_db_path)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def unique_email():
    import uuid

    return f"user-{uuid.uuid4().hex[:8]}@example.com"
