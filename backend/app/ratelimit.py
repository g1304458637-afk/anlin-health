"""Small in-process sliding-window rate limiter.

Sufficient for the single-process pilot; a multi-worker production deployment
must replace this with a shared store (e.g. Redis) — noted in DEPLOYMENT.md.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    pass


class SlidingWindowLimiter:
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        """Record one attempt; return False when the caller exceeds `limit` per window."""
        now = time.monotonic()
        with self._lock:
            bucket = self._events[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


login_limiter = SlidingWindowLimiter()
wechat_limiter = SlidingWindowLimiter()
claim_limiter = SlidingWindowLimiter()


def client_ip(request) -> str:
    # Pilot runs behind no trusted proxy; use the direct peer address.
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
