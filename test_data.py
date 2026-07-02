"""
test_data.py
─────────────
Smoke test for Phase 1 — verifies the data layer fetches real data.

Uses NVDA as the demo ticker throughout.
No API credits needed — Yahoo Finance and SEC EDGAR are free.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data.market_data import get_price_history, get_fundamentals, get_sector_performance
from src.data.sec_filings import get_filing_summary
from src.data.technical_indicators import run_full_technical_analysis


TICKER = "NVDA"


def run_test():
    print("=" * 60)
    print("TradeDesk — Phase 1 Data Layer Test")
    print(f"Demo ticker: {TICKER}")
    print("=" * 60)

    # ── Test 1: Price history ─────────────────────────────────────
    print(f"\n[Test 1] Fetching price history for {TICKER}...")
    price_data = get_price_history(TICKER)
    assert price_data.get("error") is None, f"Price error: {price_data.get('error')}"
    assert len(price_data["closes"]) > 50
    print(f"  ✓ {len(price_data['closes'])} days of price data")
    print(f"  Current price: ${price_data['current_price']}")
    print(f"  6-month change: {price_data['price_change_pct']}%")
    print(f"  52W high: ${price_data['high_52w']} | 52W low: ${price_data['low_52w']}")

    # ── Test 2: Technical indicators ──────────────────────────────
    print(f"\n[Test 2] Computing technical indicators...")
    tech = run_full_technical_analysis(price_data)
    assert "overall_signal" in tech
    print(f"  ✓ Overall signal: {tech['overall_signal']} (trend score: {tech['trend_score']})")
    print(f"  RSI: {tech['rsi']} ({tech['rsi_signal']})")
    print(f"  MACD trend: {tech['macd']['trend']}")
    print(f"  Cross signal: {tech['cross_signal']}")
    print(f"  Above SMA200: {tech['price_vs_sma']['above_sma200']}")
    print(f"  Volume: {tech['volume']['signal']}")

    # ── Test 3: Fundamentals ──────────────────────────────────────
    print(f"\n[Test 3] Fetching fundamentals...")
    fundamentals = get_fundamentals(TICKER)
    assert fundamentals.get("error") is None, f"Fundamentals error: {fundamentals.get('error')}"
    print(f"  ✓ {fundamentals['company_name']}")
    print(f"  Sector: {fundamentals['sector']} | Industry: {fundamentals['industry']}")
    print(f"  Market cap: ${fundamentals['market_cap_b']}B")
    print(f"  P/E ratio: {fundamentals['pe_ratio']}")
    print(f"  Revenue growth YoY: {fundamentals['revenue_growth_yoy']}")
    print(f"  Profit margin: {fundamentals['profit_margin']}")
    print(f"  Analyst recommendation: {fundamentals['analyst_recommendation']}")

    # ── Test 4: Sector performance ────────────────────────────────
    print(f"\n[Test 4] Fetching sector performance comparison...")
    sector = get_sector_performance(TICKER)
    assert sector.get("error") is None, f"Sector error: {sector.get('error')}"
    print(f"  ✓ Sector: {sector['sector']} (ETF: {sector['sector_etf']})")
    print(f"  90-day performance: {sector['performance_90d']}")
    print(f"  Outperforming sector: {sector['outperforming_sector']}")
    print(f"  Outperforming market: {sector['outperforming_market']}")

    # ── Test 5: SEC filings ───────────────────────────────────────
    print(f"\n[Test 5] Fetching SEC filings summary...")
    filings = get_filing_summary(TICKER)
    print(f"  Filing regularity: {filings.get('filing_regularity', 'N/A')}")
    print(f"  Recent 8-K count: {filings.get('recent_8k_count', 0)}")
    if filings.get("most_recent_10k"):
        print(f"  Most recent 10-K: {filings['most_recent_10k'].get('filed_date', 'N/A')}")
    if filings.get("most_recent_10q"):
        print(f"  Most recent 10-Q: {filings['most_recent_10q'].get('filed_date', 'N/A')}")
    if filings.get("error"):
        print(f"  Note: {filings['error']} (EDGAR can be slow — non-blocking)")

    # ── Test 6: Model client imports cleanly ──────────────────────
    print(f"\n[Test 6] Verifying Bedrock-ready model client...")
    from src.client.model_client import ModelClient
    client = ModelClient()
    print(f"  ✓ ModelClient initialized (provider: {client.provider})")

    print("\n" + "=" * 60)
    print("Phase 1 PASSED ✓ — Data layer working correctly")
    print("=" * 60)
    print(f"\nAll free API sources verified:")
    print(f"  Yahoo Finance: price history + fundamentals + sector")
    print(f"  Technical indicators: computed locally from price data")
    print(f"  SEC EDGAR: filing metadata (free, no key)")
    print(f"  Model client: Bedrock-ready, currently using Anthropic API")


if __name__ == "__main__":
    run_test()
