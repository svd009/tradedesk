"""
analysis_cache.py
───────────────────
Daily, shared cache for full TradeDesk analyses, with cache-stampede
protection. Backed by SQLite (via storage.py) so cached results survive
app restarts, rather than an in-memory dict that would reset every time.

Why this exists:
  Without it, every single request for a ticker re-runs the full 5-agent
  + synthesis pipeline, real AI cost and 30-90+ seconds, even if someone
  else asked about the exact same stock 30 seconds ago. Most traffic to
  a tool like this clusters around a handful of popular tickers, so this
  is the single highest-leverage cost and speed optimization available.

The reset boundary:
  Rather than a rolling "N hours since first computed" TTL, this uses a
  fixed daily boundary: 6 AM US Eastern time. Everyone asking about a
  ticker between one day's 6 AM ET and the next day's 6 AM ET gets the
  same cached result; the first request after each boundary triggers a
  fresh run. This is deliberately NOT "cache forever" or "always live",
  it's a middle ground chosen specifically to cut cost/latency while
  still refreshing daily. The live price chart is intentionally NOT
  part of this cache — it always fetches fresh, independent of this file.

Cache stampede protection:
  If 5 people ask about the same newly-uncached ticker within the same
  few seconds (e.g. right after the 6 AM reset), naively they'd each
  trigger their own full analysis — 5x the cost for one answer. A
  per-ticker lock fixes this: the first request acquires the lock and
  does the real work; the other four block on that same lock, then
  read the now-populated cache instead of duplicating it. These locks
  stay in memory on purpose — a lock only matters while a computation
  is actively in flight in the current process, there's nothing to
  persist about it across a restart (whatever was "in flight" when the
  process died needs to just run again anyway).

Timezone note: uses zoneinfo (Python's built-in timezone library) with
"America/New_York" rather than a hardcoded EDT/EST offset, so daylight
saving transitions are handled correctly automatically.
"""

import hashlib
import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.orchestrator import storage

_RESET_HOUR_ET = 6  # 6 AM US Eastern — the daily reset boundary
_ET = ZoneInfo("America/New_York")

_cache_lock = threading.Lock()  # protects _ticker_locks itself (see below)

# One lock per (ticker, window) — created on demand, guarded by
# _cache_lock so two threads can't race to create two different lock
# objects for the same key (the classic double-checked-locking pitfall).
# Intentionally still an in-memory dict — see the module docstring above
# for why locks specifically don't need to persist.
_ticker_locks: dict = {}


def _current_window() -> str:
    """
    Which 6AM-ET-to-6AM-ET window are we in right now, as a date string.
    Before 6 AM ET, we're still in the window that "belongs" to yesterday's
    date (it started yesterday at 6 AM and runs until today at 6 AM).
    """
    now_et = datetime.now(_ET)
    window_date = now_et.date()
    if now_et.hour < _RESET_HOUR_ET:
        window_date = window_date - timedelta(days=1)
    return window_date.isoformat()


def _portfolio_sig(portfolio: dict = None) -> str:
    """Same portfolio-hashing logic as before, just pulled into its own
    function now that storage.py needs it as a separate column rather
    than baked into one combined string key."""
    if not portfolio:
        return "none"
    return hashlib.md5(
        json.dumps(portfolio, sort_keys=True).encode()
    ).hexdigest()[:10]


def _lock_key(ticker: str, window: str, portfolio_sig: str) -> str:
    """Key for the in-memory stampede lock only — never touches SQLite."""
    return f"{ticker.upper()}:{window}:{portfolio_sig}"


def _get_ticker_lock(key: str) -> threading.Lock:
    with _cache_lock:
        if key not in _ticker_locks:
            _ticker_locks[key] = threading.Lock()
        return _ticker_locks[key]


def get_or_compute(ticker: str, compute_fn, portfolio: dict = None,
                   force_refresh: bool = False) -> tuple:
    """
    Return a cached analysis if one exists for the current window, or
    compute a fresh one (with stampede protection) if not.

    Args:
        ticker:        the stock ticker
        compute_fn:    a zero-arg callable that runs the real, expensive
                       analysis (e.g. lambda: TradeDesk().analyze(ticker, ...))
        portfolio:     optional portfolio dict, changes the cache key
        force_refresh: bypass the cache entirely and recompute, used by
                       the UI's manual "Refresh" button

    Returns:
        (result: dict, was_cached: bool, cached_at: datetime)
    """
    ticker = ticker.upper()
    window = _current_window()
    portfolio_sig = _portfolio_sig(portfolio)

    if not force_refresh:
        hit = storage.get_cached(ticker, window, portfolio_sig)
        if hit:
            result, cached_at = hit
            return result, True, cached_at

    # Not cached (or forced) — acquire this ticker's lock before doing the
    # real work, so concurrent requests for the same ticker queue up
    # instead of all running the expensive pipeline simultaneously.
    lock = _get_ticker_lock(_lock_key(ticker, window, portfolio_sig))
    with lock:
        # Double-checked: another thread may have finished computing this
        # exact key while we were waiting for the lock. If so, use that
        # result instead of computing it a second time.
        if not force_refresh:
            hit = storage.get_cached(ticker, window, portfolio_sig)
            if hit:
                result, cached_at = hit
                return result, True, cached_at

        result = compute_fn()
        cached_at = datetime.now(_ET)
        storage.save_analysis(ticker, window, portfolio_sig, result, cached_at)
        return result, False, cached_at


def cache_stats() -> dict:
    """For debugging/monitoring — usage numbers straight from the durable log."""
    stats = storage.get_usage_stats()
    stats["current_window"] = _current_window()
    return stats
