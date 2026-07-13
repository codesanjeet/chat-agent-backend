"""
Sliding-window rate limiter, in-process memory only.

Drop-in usage (matches every existing call site unchanged):

    from app import rate_limiter

    if not await rate_limiter.check(f"otp_request_ip:{ip}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")

Design notes
------------
- Uses asyncio.Lock, not threading.Lock: this app is async (Uvicorn event
  loop), and an asyncio.Lock is the correct primitive here -- a threading.Lock
  would still "work" since the critical section is short and non-blocking,
  but asyncio.Lock is the idiomatic choice and avoids ever blocking the loop
  if the critical section grows later.
- One shared bucket dict keyed by whatever string you pass in (e.g.
  "otp_request_ip:1.2.3.4"), so a single limiter instance handles every
  distinct rate limit in your app (different max_attempts/window_seconds per
  call), same as your current code -- no per-endpoint instances required.
- retry_after() is provided separately so callers who want to send a
  Retry-After header can, without changing the boolean check() contract that
  your routes already depend on.

Single-process caveat
----------------------
Correct as long as you run ONE Uvicorn worker / one instance. Add a second
worker or scale to multiple machines and each process gets its own counters,
so a client could get up to max_attempts PER PROCESS instead of one global
limit. When you outgrow this: swap the body of `check()` for a Redis
INCR + EXPIRE (or a ZSET sliding window for precision), keeping the same
function signature so no call site changes.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

_MAX_TRACKED_KEYS = 1000  # opportunistic cleanup threshold


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, max_attempts: int, window_seconds: int) -> bool:
        """Record a call attempt. Returns True if allowed, False if the key
        is currently rate-limited (call was NOT recorded when False)."""
        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            hits = self._hits[key]

            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= max_attempts:
                return False

            hits.append(now)

            if len(self._hits) > _MAX_TRACKED_KEYS:
                self._hits = defaultdict(
                    deque, {k: v for k, v in self._hits.items() if v}
                )
            return True

    async def retry_after(self, key: str, window_seconds: int) -> int:
        """Seconds until the oldest recorded hit for this key expires out of
        the window. Use this if you want to send a Retry-After header after
        check() returns False -- call it with the same key/window you just
        checked. Returns 0 if there's no active bucket for the key."""
        now = time.monotonic()
        async with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, int(hits[0] - (now - window_seconds)) + 1)

    async def reset(self, key: str) -> None:
        """Clear a key's bucket entirely -- e.g. after a successful OTP
        verify, to let the user immediately request fresh actions."""
        async with self._lock:
            self._hits.pop(key, None)


_limiter = InMemoryRateLimiter()

# Module-level functions so `from app import rate_limiter` +
# `rate_limiter.check(...)` / `rate_limiter.retry_after(...)` keep working
# exactly as every existing route already calls them.


async def check(key: str, max_attempts: int, window_seconds: int) -> bool:
    return await _limiter.check(key, max_attempts, window_seconds)


async def retry_after(key: str, window_seconds: int) -> int:
    return await _limiter.retry_after(key, window_seconds)


async def reset(key: str) -> None:
    await _limiter.reset(key)