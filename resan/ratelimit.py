"""Small in-memory sliding-window rate limiter.

Good enough for a single server process. Behind several workers or instances,
swap it for a shared store such as Redis (same interface).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock=time.monotonic) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._clock = clock

    def hit(self, key: str, limit: int, window_seconds: float) -> bool:
        """Record one hit; return False if ``key`` is over ``limit`` in the window."""
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
