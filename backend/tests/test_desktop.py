"""The desktop build's plumbing: settings derived from a data folder, the
Redis-free broker selection, and the API serving a built frontend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.celery_app import broker_url, result_backend
from app.config import Settings
from app.desktop import environment, paths
from app.main import _serve_frontend


@pytest.fixture
def clean_env(monkeypatch):
    for key in (
        "PACKRAT_DATA_DIR",
        "PACKRAT_FRONTEND_DIST",
        "PACKRAT_PORT",
        "PACKRAT_HOST",
        "DATABASE_URL",
        "CELERY_BROKER_URL",
        "LOG_DIR",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(key, raising=False)
    # prepare_environment writes into os.environ; keep that out of the
    # other tests' process state.
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# --- env file round trip -------------------------------------------------------


def test_env_file_round_trip(tmp_path: Path):
    target = tmp_path / "packrat.env"
    environment.write_env_file(target, {"A": "1", "B": "two words", "C": ""})
    assert environment.read_env_file(target) == {"A": "1", "B": "two words", "C": ""}
    target.write_text('# comment\n\nX = "quoted"\nBROKEN LINE\nY=\'single\'\n', encoding="utf-8")
    assert environment.read_env_file(target) == {"X": "quoted", "Y": "single"}
    assert environment.read_env_file(tmp_path / "missing.env") == {}


def test_prepare_environment_generates_secrets_once_and_derives_paths(tmp_path: Path, clean_env, monkeypatch):
    monkeypatch.setattr(paths, "frontend_dist", lambda: None)
    monkeypatch.setattr(environment, "frontend_dist", lambda: None)
    data = tmp_path / "data"

    values = environment.prepare_environment(data)

    assert (data / "logs").is_dir() and (data / "firmware_images").is_dir()
    assert values["ENVIRONMENT"] == "production"
    assert values["DATABASE_URL"] == f"sqlite+aiosqlite:///{(data / 'packrat.db').resolve().as_posix()}"
    assert values["CELERY_BROKER_URL"] == f"sqla+sqlite:///{(data / 'queue.db').resolve().as_posix()}"
    assert values["CELERY_TASK_ALWAYS_EAGER"] == "false"
    assert values["LOG_DIR"] == str(data / "logs")
    assert values["PACKRAT_PORT"] == str(paths.DEFAULT_PORT)
    assert "FRONTEND_DIST_DIR" not in values
    assert os.environ["JWT_SECRET_KEY"] == values["JWT_SECRET_KEY"]
    assert len(values["JWT_SECRET_KEY"]) >= 32
    # The generated secrets must satisfy the backend's own refusal check.
    settings = Settings(_env_file=None)
    from app.config import insecure_secrets  # noqa: PLC0415

    assert insecure_secrets(settings) == []

    # Second run: same secrets, no regeneration; the file's own entries win.
    stored = environment.read_env_file(data / environment.ENV_FILE)
    stored["PACKRAT_PORT"] = "9000"
    stored["LOG_LEVEL"] = "DEBUG"
    stored["ENVIRONMENT"] = "development"  # not honoured: the desktop build is always production
    environment.write_env_file(data / environment.ENV_FILE, stored)
    again = environment.prepare_environment(data)
    assert again["JWT_SECRET_KEY"] == values["JWT_SECRET_KEY"]
    assert again["CREDENTIAL_ENCRYPTION_KEY"] == values["CREDENTIAL_ENCRYPTION_KEY"]
    assert again["PACKRAT_PORT"] == "9000"
    assert again["LOG_LEVEL"] == "DEBUG"
    assert again["ENVIRONMENT"] == "production"


def test_prepare_environment_points_at_a_frontend_build(tmp_path: Path, clean_env, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>Packrat</title>")
    monkeypatch.setattr(environment, "frontend_dist", lambda: dist)
    values = environment.prepare_environment(tmp_path / "data")
    assert values["FRONTEND_DIST_DIR"] == str(dist)


def test_prepare_broker_creates_the_queue_tables(tmp_path: Path):
    url = f"sqla+sqlite:///{(tmp_path / 'queue.db').as_posix()}"
    environment.prepare_broker(url)
    import sqlite3  # noqa: PLC0415

    tables = {row[0] for row in sqlite3.connect(tmp_path / "queue.db").execute("select name from sqlite_master where type='table'")}
    assert {"kombu_queue", "kombu_message"} <= tables
    # Redis URLs are left to the worker (nothing to pre-create).
    environment.prepare_broker("redis://localhost:6379/0")


def test_data_dir_override_and_platform_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("PACKRAT_DATA_DIR", "/somewhere/else")
    assert paths.data_dir() == Path("/somewhere/else")
    monkeypatch.delenv("PACKRAT_DATA_DIR")
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\pat\AppData\Local")
    assert paths.data_dir() == Path(r"C:\Users\pat\AppData\Local") / "Packrat"
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.data_dir() == Path.home() / "Library" / "Application Support" / "Packrat"
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg")
    assert paths.data_dir() == Path("/xdg/packrat")


# --- broker / result backend selection -------------------------------------------


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_broker_defaults_to_redis_and_uses_it_for_results():
    s = _settings(redis_url="redis://box:6379/2")
    assert broker_url(s) == "redis://box:6379/2"
    assert result_backend(s) == "redis://box:6379/2"


def test_sqlite_broker_runs_without_a_result_backend():
    s = _settings(celery_broker_url="sqla+sqlite:////data/queue.db")
    assert broker_url(s) == "sqla+sqlite:////data/queue.db"
    assert result_backend(s) is None


def test_explicit_result_backend_wins():
    assert result_backend(_settings(celery_result_backend="db+sqlite:///r.db")) == "db+sqlite:///r.db"
    assert result_backend(_settings(celery_result_backend="none")) is None
    assert result_backend(_settings(celery_result_backend="NONE", redis_url="redis://x")) is None


# --- serving the frontend ----------------------------------------------------------


def test_serve_frontend_files_spa_fallback_and_api_404(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Packrat</title>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('hi')", encoding="utf-8")
    (dist / "sw.js").write_text("// worker", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")

    application = FastAPI()

    @application.get("/api/things")
    async def things():
        return ["a"]

    assert _serve_frontend(str(dist), application) is True
    client = TestClient(application)

    assert client.get("/api/things").json() == ["a"]
    assert client.get("/api/missing").status_code == 404
    assert client.get("/api").status_code == 404

    home = client.get("/")
    assert home.status_code == 200 and "<title>Packrat</title>" in home.text
    assert home.headers["cache-control"] == "no-cache"
    # Client-side routes get the shell too.
    assert "<title>Packrat</title>" in client.get("/jobs/123").text
    # Real files come back as themselves.
    assert client.get("/assets/app-abc123.js").text == "console.log('hi')"
    assert client.get("/sw.js").status_code == 200 and "javascript" in client.get("/sw.js").headers["content-type"]
    # Nothing outside the dist folder is reachable.
    assert "nope" not in client.get("/../secret.txt").text
    assert "nope" not in client.get("/%2e%2e/secret.txt").text


def test_serve_frontend_refuses_a_folder_without_a_build(tmp_path: Path):
    application = FastAPI()
    assert _serve_frontend(str(tmp_path), application) is False
    assert TestClient(application).get("/").status_code == 404
