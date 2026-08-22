"""
market_data.py
───────────────
Fetches price history and fundamental data from Yahoo Finance via yfinance.

Why Yahoo Finance?
  Free, no API key required, covers 99% of publicly traded stocks,
  real-time delayed quotes, and includes fundamentals like P/E ratio,
  revenue, margins, and earnings history. The standard choice for
  portfolio projects and many production fintech tools.

What each subagent uses from here:
  Technical Analyst (SA3) → get_price_history() for OHLCV data
  Fundamentals Analyst (SA2) → get_fundamentals() for financial ratios
  Risk Analyst (SA5) → get_price_history() for correlation calculation
"""

import time
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from config import PRICE_HISTORY_DAYS, TWELVE_DATA_API_KEY

# Yahoo Finance rate-limits aggressively on shared/cloud IPs (Streamlit
# Community Cloud shares IP ranges across many apps), which shows up as a
# JSON decode error ("Expecting value: line 1 column 1") rather than a
# clear "too many requests" message. This is usually transient — a short
# retry often succeeds where the first attempt didn't.
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 2


def _with_retry(fetch_fn, ticker: str):
    """Retry a Yahoo Finance fetch a few times before giving up — but only
    for actual fetch failures (network errors, rate-limits), not for a
    ticker that yfinance successfully looked up and found nothing for.
    The latter is a genuine 'this isn't a real ticker' result; retrying
    it wastes time and the former needs the _transient_fetch_failure flag
    so the UI doesn't wrongly tell someone their real stock doesn't exist."""
    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            result = fetch_fn()
            if not result.get("error"):
                return result
            if result["error"].startswith("No price data found") or "No data found" in result["error"]:
                return result  # genuine invalid ticker — don't retry, don't mislabel
            last_error = result["error"]
        except Exception as e:
            last_error = str(e)
        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_DELAY_SECONDS)
    return {"ticker": ticker, "error": last_error, "_transient_fetch_failure": True}

# ── Lightweight TTL cache ──────────────────────────────────────────────────
# Multiple subagents (and repeat test runs on the same ticker) were each
# hitting Yahoo Finance / EDGAR from scratch, adding real seconds to every
# analysis. This is a plain in-memory cache with no Streamlit dependency,
# so it works the same whether called from the app or a subagent thread.
_CACHE_TTL_SECONDS = 300  # 5 minutes — long enough to cover one testing session
_cache: dict = {}


def _cached(key, fetch_fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    value = fetch_fn()
    # Only cache real successes. Caching an error would mean a transient
    # Yahoo Finance rate-limit gets "frozen" and re-served to everyone for
    # the full TTL window, even after Yahoo's own block clears seconds later.
    if not value.get("error"):
        _cache[key] = (now, value)
    return value


def get_price_history(ticker: str, days: int = PRICE_HISTORY_DAYS) -> dict:
    """
    Fetch OHLCV price history for a ticker (cached for 5 minutes).

    Args:
        ticker: Stock ticker symbol e.g. "NVDA"
        days:   Number of calendar days of history

    Returns:
        {
          "ticker": str,
          "period_days": int,
          "current_price": float,
          "price_change_pct": float,   ← % change over the period
          "high_52w": float,
          "low_52w": float,
          "avg_volume": int,
          "dates": list[str],
          "opens": list[float],   ← added for candlestick charting
          "highs": list[float],   ← added for candlestick charting
          "lows": list[float],    ← added for candlestick charting
          "closes": list[float],
          "volumes": list[int],
          "error": str or None,
        }
    """
    return _cached(f"price:{ticker}:{days}", lambda: _fetch_with_fallback(ticker, days))


def _fetch_with_fallback(ticker: str, days: int) -> dict:
    """
    Try Yahoo Finance first (with its own retries). If Yahoo is genuinely
    unreachable (not just "this ticker doesn't exist"), fall back to
    Twelve Data before giving up entirely. This is a circuit-breaker-style
    pattern: primary source first, secondary source only on real failure,
    the caller never needs to know which one actually answered.
    """
    result = _with_retry(lambda: _fetch_price_history(ticker, days), ticker)
    if not result.get("_transient_fetch_failure"):
        return result  # success, or a genuine "no such ticker" — either way, done

    if not TWELVE_DATA_API_KEY:
        return result  # no fallback configured — return Yahoo's honest failure

    fallback_result = _fetch_price_history_twelvedata(ticker, days)
    if fallback_result.get("closes"):
        fallback_result["_served_by"] = "twelvedata"  # for debugging/logs only
        return fallback_result

    return result  # fallback also failed — return Yahoo's original failure info


def _twelvedata_symbol(ticker: str) -> tuple:
    """
    Translate our yfinance-style ticker (e.g. RELIANCE.NS) into Twelve
    Data's format, which wants the base symbol and exchange separately
    (e.g. symbol=RELIANCE, exchange=NSE).
    """
    if ticker.endswith(".NS"):
        return ticker[:-3], "NSE"
    if ticker.endswith(".BO"):
        return ticker[:-3], "BSE"
    return ticker, None  # US ticker — no exchange param needed


def _fetch_price_history_twelvedata(ticker: str, days: int) -> dict:
    try:
        symbol, exchange = _twelvedata_symbol(ticker)
        params = {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": min(days, 5000),
            "apikey": TWELVE_DATA_API_KEY,
        }
        if exchange:
            params["exchange"] = exchange

        resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=15)
        data = resp.json()

        if data.get("status") == "error" or "values" not in data:
            return {"ticker": ticker, "error": data.get("message", "Twelve Data returned no data")}

        values = list(reversed(data["values"]))  # Twelve Data returns newest-first
        opens = [float(v["open"]) for v in values]
        highs = [float(v["high"]) for v in values]
        lows = [float(v["low"]) for v in values]
        closes = [float(v["close"]) for v in values]
        volumes = [int(float(v.get("volume", 0))) for v in values]
        dates = [v["datetime"] for v in values]

        current_price = closes[-1]
        start_price = closes[0]
        price_change_pct = ((current_price - start_price) / start_price) * 100 if start_price else 0.0

        return {
            "ticker": ticker,
            "period_days": days,
            "current_price": round(current_price, 2),
            "price_change_pct": round(price_change_pct, 2),
            "high_52w": round(max(highs), 2),
            "low_52w": round(min(lows), 2),
            "avg_volume": int(sum(volumes) / len(volumes)) if volumes else 0,
            "currency": data.get("meta", {}).get("currency", "USD"),
            "dates": dates,
            "opens": [round(o, 2) for o in opens],
            "highs": [round(h, 2) for h in highs],
            "lows": [round(l, 2) for l in lows],
            "closes": [round(c, 2) for c in closes],
            "volumes": volumes,
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def _fetch_price_history(ticker: str, days: int) -> dict:
    try:
        stock = yf.Ticker(ticker)
        end = datetime.now()
        start = end - timedelta(days=days)
        hist = stock.history(start=start, end=end)

        if hist.empty:
            return {"ticker": ticker, "error": f"No price data found for {ticker}"}

        opens = hist["Open"].tolist()
        highs = hist["High"].tolist()
        lows = hist["Low"].tolist()
        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        dates = [d.strftime("%Y-%m-%d") for d in hist.index]

        current_price = closes[-1]
        start_price = closes[0]
        price_change_pct = ((current_price - start_price) / start_price) * 100

        info = stock.info
        high_52w = info.get("fiftyTwoWeekHigh", max(closes))
        low_52w = info.get("fiftyTwoWeekLow", min(closes))
        currency = info.get("currency", "USD")  # e.g. "INR" for .NS/.BO tickers

        return {
            "ticker": ticker,
            "period_days": days,
            "current_price": round(current_price, 2),
            "price_change_pct": round(price_change_pct, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "avg_volume": int(sum(volumes) / len(volumes)),
            "currency": currency,
            "dates": dates,
            "opens": [round(o, 2) for o in opens],
            "highs": [round(h, 2) for h in highs],
            "lows": [round(l, 2) for l in lows],
            "closes": [round(c, 2) for c in closes],
            "volumes": volumes,
            "error": None,
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def get_fundamentals(ticker: str) -> dict:
    """
    Fetch key fundamental metrics for a ticker (cached for 5 minutes).

    Returns the most important valuation and financial health metrics
    that an equity analyst would review — in a format the fundamentals
    subagent can reason over directly.
    """
    return _cached(f"fundamentals:{ticker}", lambda: _with_retry(lambda: _fetch_fundamentals(ticker), ticker))


def _truncate_at_word(text: str, max_length: int) -> str:
    """
    Truncate at the last full word before max_length, with an ellipsis.
    A hard character cutoff (e.g. "...It also provides sed") reads as
    broken; this makes it clear the summary was intentionally shortened.
    """
    if not text or len(text) <= max_length:
        return text
    truncated = text[:max_length].rsplit(" ", 1)[0]
    return truncated.rstrip(".,;: ") + "…"


def _fetch_fundamentals(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # Pull earnings history if available
        earnings_df = stock.earnings_history
        recent_earnings = []
        if earnings_df is not None and not earnings_df.empty:
            for _, row in earnings_df.head(4).iterrows():
                recent_earnings.append({
                    "period": str(row.get("period", "")),
                    "eps_estimate": row.get("epsEstimate"),
                    "eps_actual": row.get("epsActual"),
                    "surprise_pct": row.get("surprisePercent"),
                })

        return {
            "ticker": ticker,
            "company_name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap_b": round(info.get("marketCap", 0) / 1e9, 2),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "revenue_growth_yoy": info.get("revenueGrowth"),
            "earnings_growth_yoy": info.get("earningsGrowth"),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "profit_margin": info.get("profitMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "return_on_assets": info.get("returnOnAssets"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "free_cash_flow_b": round(info.get("freeCashflow", 0) / 1e9, 2)
                                if info.get("freeCashflow") else None,
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "analyst_target_price": info.get("targetMeanPrice"),
            "analyst_recommendation": info.get("recommendationKey"),
            "recent_earnings_surprises": recent_earnings,
            "business_summary": _truncate_at_word(info.get("longBusinessSummary", ""), 1200),
            "error": None,
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def get_sector_performance(ticker: str) -> dict:
    """
    Get the ticker's sector ETF performance as a macro context proxy.
    Maps common sectors to their representative ETF for comparison.
    """
    sector_etfs = {
        "Technology": "XLK",
        "Financial Services": "XLF",
        "Healthcare": "XLV",
        "Consumer Cyclical": "XLY",
        "Consumer Defensive": "XLP",
        "Energy": "XLE",
        "Industrials": "XLI",
        "Basic Materials": "XLB",
        "Real Estate": "XLRE",
        "Utilities": "XLU",
        "Communication Services": "XLC",
    }

    try:
        stock = yf.Ticker(ticker)
        sector = stock.info.get("sector", "")
        etf = sector_etfs.get(sector, "SPY")  # fallback to S&P 500

        # Compare ticker vs sector ETF vs SPY over 3 months
        end = datetime.now()
        start = end - timedelta(days=90)

        results = {}
        for symbol in [ticker, etf, "SPY"]:
            hist = yf.Ticker(symbol).history(start=start, end=end)
            if not hist.empty:
                perf = ((hist["Close"].iloc[-1] - hist["Close"].iloc[0])
                        / hist["Close"].iloc[0]) * 100
                results[symbol] = round(perf, 2)

        return {
            "ticker": ticker,
            "sector": sector,
            "sector_etf": etf,
            "performance_90d": results,
            "outperforming_sector": results.get(ticker, 0) > results.get(etf, 0),
            "outperforming_market": results.get(ticker, 0) > results.get("SPY", 0),
            "error": None,
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}
