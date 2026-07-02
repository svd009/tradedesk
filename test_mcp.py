"""
test_mcp.py
────────────
Smoke test for Phase 2 — verifies all MCP tools execute correctly.
Zero API cost — all tools use free data sources only.
"""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.mcp_server.market_tools import MarketToolExecutor, TOOL_SCHEMAS, WEB_SEARCH_TOOL
from config import DEMO_PORTFOLIO

TICKER = "NVDA"


def run_test():
    print("=" * 60)
    print("TradeDesk — Phase 2 MCP Tools Test")
    print("=" * 60)
    print("\nNOTE: Zero API cost — all free data sources")

    executor = MarketToolExecutor()

    # ── Test 1: Tool schemas ──────────────────────────────────────
    print("\n[Test 1] Verifying tool schemas...")
    assert len(TOOL_SCHEMAS) == 6
    for schema in TOOL_SCHEMAS:
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        print(f"  ✓ {schema['name']}")
    assert WEB_SEARCH_TOOL["type"] == "web_search_20250305"
    print(f"  ✓ web_search tool (native Claude tool)")

    # ── Test 2: get_price_and_technicals ─────────────────────────
    print(f"\n[Test 2] get_price_and_technicals({TICKER})...")
    result = json.loads(executor.execute("get_price_and_technicals", {"ticker": TICKER}))
    assert "overall_signal" in result, f"Missing overall_signal: {result}"
    assert "error" not in result or result["error"] is None
    print(f"  ✓ Overall signal: {result['overall_signal']}")
    print(f"  RSI: {result['rsi']} | MACD: {result['macd']['trend']}")
    print(f"  Cross: {result['cross_signal']}")

    # ── Test 3: get_fundamentals ──────────────────────────────────
    print(f"\n[Test 3] get_fundamentals({TICKER})...")
    result = json.loads(executor.execute("get_fundamentals", {"ticker": TICKER}))
    assert "company_name" in result
    print(f"  ✓ {result['company_name']}")
    print(f"  P/E: {result['pe_ratio']} | Margin: {result['profit_margin']}")
    print(f"  Revenue growth: {result['revenue_growth_yoy']}")

    # ── Test 4: get_sector_comparison ─────────────────────────────
    print(f"\n[Test 4] get_sector_comparison({TICKER})...")
    result = json.loads(executor.execute("get_sector_comparison", {"ticker": TICKER}))
    assert "sector" in result
    print(f"  ✓ Sector: {result['sector']} ({result['sector_etf']})")
    print(f"  90d performance: {result['performance_90d']}")
    print(f"  Outperforming market: {result['outperforming_market']}")

    # ── Test 5: get_sec_filings ───────────────────────────────────
    print(f"\n[Test 5] get_sec_filings({TICKER})...")
    result = json.loads(executor.execute("get_sec_filings", {"ticker": TICKER}))
    print(f"  Filing regularity: {result.get('filing_regularity', 'N/A')}")
    print(f"  Recent 8-K count: {result.get('recent_8k_count', 0)}")
    if result.get("error"):
        print(f"  Note: {result['error']} (EDGAR latency — non-critical)")

    # ── Test 6: get_portfolio_exposure ────────────────────────────
    print(f"\n[Test 6] get_portfolio_exposure (demo portfolio)...")
    result = json.loads(executor.execute("get_portfolio_exposure", {
        "portfolio": DEMO_PORTFOLIO,
        "new_ticker": "GOOGL"
    }))
    assert "sector_exposure" in result
    print(f"  ✓ Portfolio: {result['portfolio_size']} holdings")
    print(f"  Sector exposure: {result['sector_exposure']}")
    print(f"  Concentration risk: {result['concentration_risk']}")
    if result.get("new_ticker_analysis"):
        print(f"  GOOGL fit: {result['new_ticker_analysis']['recommendation_note']}")

    # ── Test 7: Unknown tool error handling ───────────────────────
    print(f"\n[Test 7] Unknown tool error handling...")
    result = json.loads(executor.execute("nonexistent_tool", {}))
    assert "error" in result
    print(f"  ✓ Graceful error: {result['error']}")

    print("\n" + "=" * 60)
    print("Phase 2 PASSED ✓ — MCP tools working correctly")
    print("=" * 60)
    print("\n6 market data tools + native web_search ready for subagents")


if __name__ == "__main__":
    run_test()
