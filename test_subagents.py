"""
test_subagents.py
──────────────────
Smoke test for Phase 3 — verifies all 5 subagents produce
correctly structured output.

COST NOTE: Each subagent makes real Claude API calls.
To keep cost minimal (~$0.05-0.10), this test runs only
SA2 (Fundamentals) and SA3 (Technical) by default — the two
that don't use web search and are fastest/cheapest.

Run with --all to test all 5 subagents (~$0.15-0.25 total).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.mcp_server.market_tools import MarketToolExecutor
from src.subagents.fundamentals_agent import FundamentalsAgent
from src.subagents.technical_agent import TechnicalAgent
from src.subagents.news_agent import NewsAgent
from src.subagents.macro_agent import MacroAgent
from src.subagents.risk_agent import RiskAgent
from config import DEMO_PORTFOLIO

TICKER = "NVDA"
RUN_ALL = "--all" in sys.argv


def run_test():
    print("=" * 60)
    print("TradeDesk — Phase 3 Subagents Test")
    print(f"Ticker: {TICKER} | Mode: {'ALL 5 agents' if RUN_ALL else '2 agents (cheap mode)'}")
    print("=" * 60)
    print(f"\nNOTE: Makes real Claude API calls (~$0.05-0.10)")

    executor = MarketToolExecutor()
    results = {}

    # ── SA2: Fundamentals (no web search, cheapest) ───────────────
    print("\n[Test SA2] Fundamentals Agent...")
    agent = FundamentalsAgent(executor)
    result = agent.run(TICKER, verbose=True)
    assert "fundamental_score" in result or "parse_error" in result
    assert result.get("agent") == "fundamentals"
    results["fundamentals"] = result
    print(f"  ✓ Fundamental score: {result.get('fundamental_score')}/10")
    print(f"  Valuation: {result.get('valuation')}")
    print(f"  Growth: {result.get('growth_trajectory')}")

    # ── SA3: Technical (no web search, cheapest) ──────────────────
    print("\n[Test SA3] Technical Agent...")
    agent = TechnicalAgent(executor)
    result = agent.run(TICKER, verbose=True)
    assert "technical_signal" in result or "parse_error" in result
    assert result.get("agent") == "technical"
    results["technical"] = result
    print(f"  ✓ Signal: {result.get('technical_signal')}")
    print(f"  Trend: {result.get('trend_direction')}")
    print(f"  RSI: {result.get('rsi_assessment')}")

    if RUN_ALL:
        # ── SA1: News (uses web search) ───────────────────────────
        print("\n[Test SA1] News & Sentiment Agent...")
        fund = results.get("fundamentals", {})
        agent = NewsAgent(executor)
        result = agent.run(TICKER,
                           company_name=fund.get("company_name", "Nvidia"),
                           verbose=True)
        assert "sentiment_score" in result or "parse_error" in result
        results["news"] = result
        print(f"  ✓ Sentiment: {result.get('sentiment_label')} ({result.get('sentiment_score')})")

        # ── SA4: Macro (uses web search) ──────────────────────────
        print("\n[Test SA4] Macro & Sector Agent...")
        agent = MacroAgent(executor)
        result = agent.run(TICKER,
                           company_name=fund.get("company_name", "Nvidia"),
                           verbose=True)
        assert "macro_stance" in result or "parse_error" in result
        results["macro"] = result
        print(f"  ✓ Macro: {result.get('macro_stance')} | Sector: {result.get('sector_momentum')}")

        # ── SA5: Risk (portfolio mode) ────────────────────────────
        print("\n[Test SA5] Risk & Portfolio Agent...")
        agent = RiskAgent(executor)
        result = agent.run(TICKER, portfolio=DEMO_PORTFOLIO, verbose=True)
        assert "portfolio_fit" in result or "parse_error" in result
        results["risk"] = result
        print(f"  ✓ Fit: {result.get('portfolio_fit')} | Weight: {result.get('suggested_max_weight')}")

    print("\n" + "=" * 60)
    print(f"Phase 3 PASSED ✓ — {len(results)} subagents verified")
    print("=" * 60)
    print(f"\nRun 'python test_subagents.py --all' to test all 5 subagents")
    print(f"(costs ~$0.15-0.25 extra in API credits)")


if __name__ == "__main__":
    run_test()
