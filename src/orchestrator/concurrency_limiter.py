"""
concurrency_limiter.py
────────────────────────
A concurrency cap: how many analyses can run AT THE SAME TIME, across
every user combined. This is a genuinely different concern from the
token bucket rate limiter (src/orchestrator/rate_limiter.py), which
caps how often ONE person can ask, not how many people can be running
an analysis simultaneously right now.

Why this doesn't need to be persistent (unlike the analysis cache):
  In-flight concurrency is meaningless once the app restarts — whatever
  was "in progress" needs to run again from scratch anyway. A plain
  in-memory counter, protected by a lock, is the correct and sufficient
  tool here, the same reasoning that keeps the cache-stampede locks in
  analysis_cache.py in-memory rather than in DynamoDB.

Why the cap only applies to real computation, not cache hits:
  Serving an already-cached result costs nothing expensive, there's no
  reason to make someone queue behind 10 "heavy" analyses just to read
  a cache entry. See where this is wired into analysis_cache.py's
  get_or_compute() — the check happens only on the cache-miss path,
  right before the actual 5-agent pipeline would run.
"""

import threading


class ConcurrencyLimitExceeded(Exception):
    """Raised when the cap is already at capacity and a new analysis
    can't start right now. The caller (app.py) catches this and shows
    a 'heavy traffic, try again shortly' message instead of a crash."""
    pass


class ConcurrencyLimiter:
    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self._current = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """Attempt to claim a slot. Returns True if successful."""
        with self._lock:
            if self._current >= self.max_concurrent:
                return False
            self._current += 1
            return True

    def release(self):
        """Give back a slot. Safe to call even if nothing was held."""
        with self._lock:
            self._current = max(0, self._current - 1)

    def current_load(self) -> tuple:
        """(currently running, max capacity) — for status displays."""
        with self._lock:
            return self._current, self.max_concurrent
