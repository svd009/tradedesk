"""
screener.py
────────────
Discovery/filtering across the whole ticker universe, TradeDesk's
version of Screener.in and Tickertape's core idea: "find me stocks
matching X," complementing the deep single-stock analysis the rest of
the app already does.

Why a daily pre-computed snapshot, not live fetching per page load:
  Filtering hundreds of tickers live, on every visit, would mean
  hundreds of simultaneous Yahoo Finance calls — precisely the kind of
  bulk traffic that triggered real, documented rate-limiting earlier in
  this project's own history. Instead, the whole universe's basic
  metrics are fetched ONCE per day, lazily, by whoever opens the
  Screener page first that day (same reasoning as the daily analysis
  cache's 6 AM ET window, just applied to a batch of ~554 tickers
  instead of one), stored in DynamoDB, and served instantly to everyone
  else that day.

Why a conservative thread pool for the build itself:
  Even the once-a-day build touches every ticker in the universe. A
  wide thread pool here would recreate the exact bulk-traffic problem
  this design is meant to avoid. _BUILD_MAX_WORKERS is deliberately
  small — slower to build, gentler on Yahoo Finance.

Why this never runs the AI pipeline:
  The whole point of a screener is to be fast and free to browse.
  Running 5 AI subagents against 554 tickers to let someone filter
  would be enormously expensive and slow. This is pure, cheap data
  fetching; clicking into any result to get the real deep analysis is
  a separate, deliberate action.

Cache stampede protection:
  If several people open the Screener on the same day before a
  snapshot exists yet, a single process-wide lock ensures only one
  554-ticker build runs, not several simultaneously — the same
  principle as the per-ticker lock in analysis_cache.py, just scoped
  to "one build for the whole day" instead of "one build per ticker."
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.orchestrator import storage
from src.orchestrator.analysis_cache import _current_window
from src.data.market_data import get_price_history, get_fundamentals
from src.data.technical_indicators import run_full_technical_analysis
from src.data.ticker_directory import COMPANY_TO_TICKER

_BUILD_MAX_WORKERS = 5  # deliberately conservative — see module docstring
_build_lock = threading.Lock()


def _fetch_one_row(company_name: str, ticker: str) -> dict:
    """
    Fetch one ticker's screener-relevant metrics. Returns None on any
    failure — a handful of tickers not loading on a given day should
    exclude them from today's snapshot, not break the whole build.
    """
    try:
        fund = get_fundamentals(ticker)
        if fund.get("error"):
            return None
        price_data = get_price_history(ticker, days=200)
        if price_data.get("error") or not price_data.get("closes"):
            return None
        tech = run_full_technical_analysis(price_data)

        market = "India" if ticker.upper().endswith((".NS", ".BO")) else "US"

        return {
            "ticker": ticker.upper(),
            "company_name": company_name,
            "market": market,
            "sector": fund.get("sector") or "Unknown",
            "pe_ratio": fund.get("pe_ratio"),
            "peg_ratio": fund.get("peg_ratio"),
            "revenue_growth_yoy": fund.get("revenue_growth_yoy"),
            "profit_margin": fund.get("profit_margin"),
            "dividend_yield": fund.get("dividend_yield"),
            "rsi": tech.get("rsi"),
            "price_change_pct": price_data.get("price_change_pct"),
            "current_price": price_data.get("current_price"),
            "currency": price_data.get("currency", "USD"),
        }
    except Exception:
        return None


def build_screener_snapshot(progress_callback=None) -> list:
    """
    Fetches metrics for the whole ticker universe with a small,
    deliberately conservative thread pool. Returns a list of row dicts;
    tickers that failed to fetch are simply excluded.
    """
    rows = []
    universe = list(COMPANY_TO_TICKER.items())
    total = len(universe)
    completed = 0

    with ThreadPoolExecutor(max_workers=_BUILD_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_row, name, ticker): ticker for name, ticker in universe}
        for future in as_completed(futures):
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
            row = future.result()
            if row:
                rows.append(row)

    return rows


def get_or_build_snapshot(force_refresh: bool = False, progress_callback=None) -> list:
    """
    Returns today's screener snapshot, building it (once) if it doesn't
    exist yet, with stampede protection so simultaneous first-visitors
    don't each trigger their own 554-ticker build.
    """
    date_str = _current_window()

    if not force_refresh:
        existing = storage.get_screener_snapshot(date_str)
        if existing:
            return existing

    with _build_lock:
        # Double-checked: another thread may have just finished building
        # while this one was waiting for the lock.
        if not force_refresh:
            existing = storage.get_screener_snapshot(date_str)
            if existing:
                return existing

        rows = build_screener_snapshot(progress_callback=progress_callback)
        for row in rows:
            storage.save_screener_row(date_str, row["ticker"], row)
        return rows


def get_sectors(snapshot: list) -> list:
    """Distinct sectors present in a snapshot, for a filter dropdown."""
    return sorted({row.get("sector", "Unknown") for row in snapshot if row.get("sector")})


def filter_snapshot(snapshot: list, market: str = None, sector: str = None,
                    pe_max: float = None, peg_max: float = None,
                    min_revenue_growth: float = None, min_profit_margin: float = None,
                    rsi_min: float = None, rsi_max: float = None,
                    min_price_change: float = None, max_price_change: float = None,
                    min_dividend_yield: float = None) -> list:
    """
    Pure Python filtering over an already-fetched snapshot — no network
    calls, instant regardless of how many filters are applied.
    """
    results = []
    for row in snapshot:
        if market and market != "All" and row.get("market") != market:
            continue
        if sector and sector != "All" and row.get("sector") != sector:
            continue
        if pe_max is not None and (row.get("pe_ratio") is None or row["pe_ratio"] > pe_max):
            continue
        if peg_max is not None and (row.get("peg_ratio") is None or row["peg_ratio"] > peg_max):
            continue
        if min_revenue_growth is not None and (row.get("revenue_growth_yoy") is None or row["revenue_growth_yoy"] < min_revenue_growth):
            continue
        if min_profit_margin is not None and (row.get("profit_margin") is None or row["profit_margin"] < min_profit_margin):
            continue
        if rsi_min is not None and (row.get("rsi") is None or row["rsi"] < rsi_min):
            continue
        if rsi_max is not None and (row.get("rsi") is None or row["rsi"] > rsi_max):
            continue
        if min_price_change is not None and (row.get("price_change_pct") is None or row["price_change_pct"] < min_price_change):
            continue
        if max_price_change is not None and (row.get("price_change_pct") is None or row["price_change_pct"] > max_price_change):
            continue
        if min_dividend_yield is not None and (row.get("dividend_yield") is None or row["dividend_yield"] < min_dividend_yield):
            continue
        results.append(row)
    return results


# One-click filter presets — the actual slider VALUES to apply, keyed by
# the same widget keys the Screener UI already uses, so applying a
# preset is just "set these session_state values, then let the sliders
# render normally from there." Values chosen as reasonable, commonly-
# cited thresholds (RSI 30/70 for oversold/overbought is standard
# technical-analysis convention), not tuned to any particular dataset.
SCREENER_PRESETS = {
    "🔻 Oversold Value": {
        "screener_pe": 20, "screener_rsi": (0, 35),
    },
    "🚀 High Growth Momentum": {
        "screener_growth": 20, "screener_pricechg": (15, 200),
    },
    "⚠️ Overbought": {
        "screener_rsi": (70, 100),
    },
    "💰 Quality Dividend Payers": {
        "screener_dividend": 2, "screener_pe": 300,
    },
}
