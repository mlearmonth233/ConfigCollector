"""Brute-force protection for /api/auth/login.

Failed logins are counted per client IP and per account (email) over a
sliding window. Once either counter passes LOGIN_MAX_FAILURES, further
attempts are refused with 429 for a lockout that doubles with every
additional failure (30s, 60s, 120s ... capped at LOGIN_LOCKOUT_MAX_SECONDS),
so a password sprayer is slowed to a crawl while a user who mistyped
twice never notices. A successful login clears both counters.

State is in-process memory. That is the right trade for a self-hosted tool
that runs one API process; a multi-process deployment behind a load
balancer gets per-process limits, which still bound the attack rate per
process. Nothing here is a substitute for MFA on the accounts themselves.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class _Record:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    lockouts: int = 0


class LoginGuard:
    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: float = 15 * 60,
        lockout_seconds: float = 30,
        lockout_max_seconds: float = 15 * 60,
        clock=time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.lockout_max_seconds = lockout_max_seconds
        self._clock = clock
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    def _get(self, key: str) -> _Record:
        record = self._records.get(key)
        if record is None:
            record = self._records[key] = _Record()
        return record

    def _prune(self, record: _Record, now: float) -> None:
        cutoff = now - self.window_seconds
        record.failures = [t for t in record.failures if t > cutoff]

    def retry_after(self, *keys: str) -> int:
        """Seconds until the most-locked of `keys` may try again; 0 if none is locked."""
        now = self._clock()
        with self._lock:
            remaining = 0.0
            for key in keys:
                record = self._records.get(key)
                if record and record.locked_until > now:
                    remaining = max(remaining, record.locked_until - now)
            return int(remaining + 0.999) if remaining > 0 else 0

    def record_failure(self, *keys: str) -> int:
        """Counts a failed attempt against every key. Returns the lockout
        (seconds) this failure triggered, 0 if still under the threshold."""
        now = self._clock()
        triggered = 0.0
        with self._lock:
            for key in keys:
                record = self._get(key)
                self._prune(record, now)
                record.failures.append(now)
                if len(record.failures) >= self.max_failures:
                    record.lockouts += 1
                    duration = min(self.lockout_seconds * (2 ** (record.lockouts - 1)), self.lockout_max_seconds)
                    record.locked_until = max(record.locked_until, now + duration)
                    triggered = max(triggered, duration)
                    # Start a fresh count for the next escalation step.
                    record.failures = []
        return int(triggered)

    def record_success(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._records.pop(key, None)

    def sweep(self) -> None:
        """Drops idle records so the table cannot grow without bound."""
        now = self._clock()
        with self._lock:
            for key in list(self._records):
                record = self._records[key]
                self._prune(record, now)
                if not record.failures and record.locked_until <= now:
                    del self._records[key]


def _from_settings() -> LoginGuard:
    from app.config import get_settings  # noqa: PLC0415 - avoid an import cycle at module load

    s = get_settings()
    return LoginGuard(
        max_failures=s.login_max_failures,
        window_seconds=s.login_window_minutes * 60,
        lockout_seconds=s.login_lockout_seconds,
        lockout_max_seconds=s.login_lockout_max_minutes * 60,
    )


guard = _from_settings()
