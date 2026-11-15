"""
analysis_cache.py
───────────────────
Shared cache for full TradeDesk analyses, with cache-stampede protection.
Backed by DynamoDB (via storage.py) so cached results survive restarts.

Freshness (see "Market-session windows" in the code): an analysis is reused
only within the market session it was made in, and is redone early if the
price moves sharply. The "6 AM daily boundary" described below now applies
only to the Screener's once-a-day snapshot (_current_window).

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
from src.orchestrator.concurrency_limiter import ConcurrencyLimiter, ConcurrencyLimitExceeded
from config import CONCURRENCY_CAP, PRICE_MOVE_REFRESH_PCT

_RESET_HOUR_ET = 6  # 6 AM US Eastern — the daily reset boundary
_concurrency_limiter = ConcurrencyLimiter(max_concurrent=CONCURRENCY_CAP)
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


# ── Market-session windows ────────────────────────────────────────────────────
# A saved analysis is only reused inside the market session it was made in.
# Each market has four sessions per trading day, in its own local time:
#
#   pre     before the open           (news is moving, prices are not)
#   am      first half of trading
#   pm      second half of trading
#   close   after the close, until the next trading morning
#
# So someone at 2 PM gets an analysis made that afternoon, never one from
# 6 AM, and someone at midnight gets the after-close one, which is still
# correct because the market has not traded since. Weekends belong to
# Friday's "close" session. Public holidays are not modelled: a holiday is
# treated as a normal day, which costs a few extra refreshes and nothing else.
_SESSIONS = {
    "us": {
        "tz": _ET,
        # (label, start as (hour, minute)); the session runs until the next start
        "starts": [("pre", (6, 0)), ("am", (9, 30)), ("pm", (12, 45)), ("close", (16, 15))],
    },
    "in": {
        "tz": ZoneInfo("Asia/Kolkata"),
        "starts": [("pre", (7, 0)), ("am", (9, 15)), ("pm", (12, 20)), ("close", (15, 45))],
    },
}


def _market_of(ticker: str) -> str:
    return "in" if ticker.upper().endswith((".NS", ".BO")) else "us"


def _previous_trading_day(day):
    day -= timedelta(days=1)
    while day.weekday() >= 5:  # Saturday, Sunday
        day -= timedelta(days=1)
    return day


def analysis_window(ticker: str, now: datetime = None) -> str:
    """
    The market-session window a moment falls into, e.g. "2026-11-13-us-pm".
    Two requests share a cached analysis only if this string matches.
    """
    market = _market_of(ticker)
    spec = _SESSIONS[market]
    local = (now or datetime.now(spec["tz"])).astimezone(spec["tz"])
    day, minutes = local.date(), local.hour * 60 + local.minute

    first_start = spec["starts"][0][1][0] * 60 + spec["starts"][0][1][1]
    if day.weekday() >= 5 or minutes < first_start:
        # Weekend, or before this morning's first session: still the
        # previous trading day's after-close session.
        return f"{_previous_trading_day(day).isoformat()}-{market}-close"

    label = spec["starts"][0][0]
    for name, (hour, minute) in spec["starts"]:
        if minutes >= hour * 60 + minute:
            label = name
    return f"{day.isoformat()}-{market}-{label}"


def _price_moved_too_far(result: dict, current_price) -> bool:
    """
    True if the price has moved more than PRICE_MOVE_REFRESH_PCT since the
    cached analysis was made, so it should be redone even inside its window.
    """
    if not isinstance(current_price, (int, float)) or current_price <= 0:
        return False
    try:
        baseline = float(result["subagent_findings"]["technical"]["key_levels"]["current_price"])
    except (KeyError, TypeError, ValueError):
        return False
    if baseline <= 0:
        return False
    return abs(current_price - baseline) / baseline * 100 >= PRICE_MOVE_REFRESH_PCT


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
                   force_refresh: bool = False, current_price=None) -> tuple:
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

        current_price: the live price, if the caller already has it. A
                       cached analysis is discarded if the price has
                       moved more than PRICE_MOVE_REFRESH_PCT since.

    Returns:
        (result: dict, was_cached: bool, cached_at: datetime)

    Raises:
        ConcurrencyLimitExceeded: if the global concurrency cap is
        already at capacity and this would be a genuinely new (non-cached)
        computation. Cache hits are never subject to this — see the
        module docstring for why.
    """
    ticker = ticker.upper()
    window = analysis_window(ticker)
    portfolio_sig = _portfolio_sig(portfolio)

    if not force_refresh:
        hit = storage.get_cached(ticker, window, portfolio_sig)
        if hit:
            result, cached_at = hit
            if not _price_moved_too_far(result, current_price):
                return result, True, cached_at
            # The price has moved a lot since this was made (current_price
            # is the live price the caller just fetched). Fall through and
            # recompute; the new result overwrites this window's entry.

    # This is a genuine cache miss — a real, expensive analysis is about
    # to run. Check the GLOBAL concurrency cap before even attempting the
    # per-ticker lock, so a rejection is fast and doesn't contend for a
    # lock that was never the actual bottleneck.
    if not _concurrency_limiter.try_acquire():
        raise ConcurrencyLimitExceeded(
            f"Concurrency cap reached ({CONCURRENCY_CAP} analyses running at once)."
        )

    try:
        # Acquire this ticker's lock before doing the real work, so
        # concurrent requests for the same ticker queue up instead of
        # all running the expensive pipeline simultaneously.
        lock = _get_ticker_lock(_lock_key(ticker, window, portfolio_sig))
        with lock:
            # Double-checked: another thread may have finished computing
            # this exact key while we were waiting for the lock. If so,
            # use that result instead of computing it a second time.
            if not force_refresh:
                hit = storage.get_cached(ticker, window, portfolio_sig)
                if hit:
                    result, cached_at = hit
                    if not _price_moved_too_far(result, current_price):
                        return result, True, cached_at

            result = compute_fn()
            cached_at = datetime.now(_ET)
            storage.save_analysis(ticker, window, portfolio_sig, result, cached_at)
            return result, False, cached_at
    finally:
        _concurrency_limiter.release()


def concurrency_status() -> tuple:
    """(currently running analyses, max capacity) — for a status display."""
    return _concurrency_limiter.current_load()


def try_acquire_concurrency_slot() -> bool:
    """
    For call sites that don't go through get_or_compute (e.g. Portfolio
    mode, which isn't cached) but still need to respect the same global
    concurrency cap, since a portfolio analysis is just as expensive as
    a single-stock one.
    """
    return _concurrency_limiter.try_acquire()


def release_concurrency_slot():
    _concurrency_limiter.release()


def cache_stats() -> dict:
    """For debugging/monitoring — usage numbers straight from the durable log."""
    stats = storage.get_usage_stats()
    stats["current_window"] = _current_window()
    return stats
