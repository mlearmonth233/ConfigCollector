"""Eager-mode jobs on a background thread (EAGER_BACKGROUND)."""

import threading

from app.config import get_settings
from app.services.dispatch import dispatch


class FakeTask:
    name = "fake"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.ran_on = None
        self.calls = []

    def apply(self, args, kwargs):
        self.ran_on = threading.current_thread()
        self.started.set()
        self.release.wait(5)
        self.calls.append(("apply", args, kwargs))

    def apply_async(self, args, kwargs):
        self.calls.append(("apply_async", args, kwargs))


def test_background_dispatch_returns_before_the_task_finishes(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_task_always_eager", True)
    monkeypatch.setattr(settings, "eager_background", True)
    task = FakeTask()
    dispatch(task, args=["job-1"], kwargs={"otp": None})
    assert task.started.wait(5)
    assert task.calls == []  # still running: the caller was not held up
    assert task.ran_on is not threading.current_thread()
    task.release.set()
    task.ran_on.join(5)
    assert task.calls == [("apply", ["job-1"], {"otp": None})]


def test_default_dispatch_goes_through_celery(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "eager_background", False)
    task = FakeTask()
    dispatch(task, args=["x"])
    assert task.calls == [("apply_async", ["x"], {})]
