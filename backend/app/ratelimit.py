"""Database-backed sliding-window rate limiter.

Counters live in the `rate_events` table, so limits hold across processes and
workers — the previous in-process deques silently multiplied every limit by the
worker count. Keys are stored only as SHA-256 hashes (they contain IPs and
usernames). With a single database the count/insert pair is not atomic; a burst
may overshoot a limit by the number of concurrent writers, which is acceptable
for brute-force throttling. A database outage fails the request rather than
lifting the limit.
"""
import hashlib
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, func

from .models import RateEvent


_WINDOW_BACKSTOP = timedelta(hours=25)


def _key_hash(key: str) -> str:
    return hashlib.sha256(str(key).encode()).hexdigest()


class SlidingWindowLimiter:
    def __init__(self, bucket: str):
        self.bucket = bucket

    def hit(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        """Record one attempt; return False when the caller exceeds `limit` per window."""
        from .db import SessionLocal
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_seconds)
        hashed = _key_hash(key)
        with SessionLocal() as db:
            if random.random() < 0.01:
                # Opportunistic GC: rows beyond every configured window are dead weight.
                db.execute(delete(RateEvent).where(RateEvent.at < now - _WINDOW_BACKSTOP))
            seen = db.scalar(select(func.count()).select_from(RateEvent).where(
                RateEvent.bucket == self.bucket, RateEvent.key_hash == hashed,
                RateEvent.at > cutoff)) or 0
            if seen >= limit:
                db.commit()
                return False
            db.add(RateEvent(bucket=self.bucket, key_hash=hashed, at=now))
            db.commit()
            return True

    def reset(self, key: str | None = None) -> None:
        """Test/dev convenience: forget this bucket (or one key) entirely."""
        from .db import SessionLocal
        from sqlalchemy.exc import OperationalError
        with SessionLocal() as db:
            try:
                query = delete(RateEvent).where(RateEvent.bucket == self.bucket)
                if key is not None:
                    query = query.where(RateEvent.key_hash == _key_hash(key))
                db.execute(query)
                db.commit()
            except OperationalError:
                # Autouse test fixtures may reset before the schema exists;
                # a fresh table has nothing to forget anyway.
                pass


login_limiter = SlidingWindowLimiter("login")
wechat_limiter = SlidingWindowLimiter("wechat")
claim_limiter = SlidingWindowLimiter("claim")
register_limiter = SlidingWindowLimiter("register")
provision_limiter = SlidingWindowLimiter("provision")


def client_ip(request) -> str:
    # Pilot runs behind no trusted proxy; use the direct peer address.
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
