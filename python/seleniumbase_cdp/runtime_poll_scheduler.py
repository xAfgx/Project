from __future__ import annotations

import time
from typing import Any


class SingleOwnerRuntimeScheduler:
    """Give one browser owner fair runtime opportunities under sustained traffic.

    The scheduler never creates a thread and never calls the browser concurrently.
    Callers invoke it only from their existing command/event loop, so RPC/control,
    telemetry, watchdog and automatic interactions keep one serialized owner.
    """

    DEFAULT_INTERVAL_SECONDS = 0.25

    def __init__(self, adapter: Any, interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> None:
        self._adapter = adapter
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._next_poll_at = time.monotonic() + self._interval_seconds

    def queue_timeout(self, max_wait_seconds: float) -> float:
        remaining = self._next_poll_at - time.monotonic()
        return max(0.0, min(max(0.0, float(max_wait_seconds)), remaining))

    def poll_if_due(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now < self._next_poll_at:
            return False
        self._next_poll_at = now + self._interval_seconds
        self._adapter.poll_runtime()
        return True
