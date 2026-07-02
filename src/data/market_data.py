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

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from config import PRICE_HISTORY_DAYS


def get_price_history(ticker: str, days: int = PRICE_HISTORY_DAYS) -> dict:
    """
    Fetch OHLCV price history for a ticker.

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
          "closes": list[float],
          "volumes": list[int],
          "error": str or None,
        }
    """
    try:
        stock = yf.Ticker(ticker)
        end = datetime.now()
        start = end - timedelta(days=days)
        hist = stock.history(start=start, end=end)

        if hist.empty:
            return {"ticker": ticker, "error": f"No price data found for {ticker}"}

        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        dates = [d.strftime("%Y-%m-%d") for d in hist.index]

        current_price = closes[-1]
        start_price = closes[0]
        price_change_pct = ((current_price - start_price) / start_price) * 100

        info = stock.info
        high_52w = info.get("fiftyTwoWeekHigh", max(closes))
        low_52w = info.get("fiftyTwoWeekLow", min(closes))

        return {
            "ticker": ticker,
            "period_days": days,
            "current_price": round(current_price, 2),
            "price_change_pct": round(price_change_pct, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "avg_volume": int(sum(volumes) / len(volumes)),
            "dates": dates,
            "closes": [round(c, 2) for c in closes],
            "volumes": volumes,
            "error": None,
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def get_fundamentals(ticker: str) -> dict:
    """
    Fetch key fundamental metrics for a ticker.

    Returns the most important valuation and financial health metrics
    that an equity analyst would review — in a format the fundamentals
    subagent can reason over directly.
    """
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
            "business_summary": info.get("longBusinessSummary", "")[:500],
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
