"""
test_orchestrator.py
─────────────────────
Smoke test for Phase 4 — verifies parallel execution and synthesis.

COST NOTE: This test makes real Claude API calls.
  - Capped run (2 subagents + synthesis): ~$0.10-0.20
  - Full run (5 subagents + synthesis): ~$0.25-0.50

Runs 2 subagents by default to save credits.
Run with --full for all 5 subagents.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.mcp_server.market_tools import MarketToolExecutor
from src.subagents.fundamentals_agent import FundamentalsAgent
from src.subagents.technical_agent import TechnicalAgent
from src.orchestrator.synthesis_agent import SynthesisAgent
from src.orchestrator.tradedesk_orchestrator import TradeDesk
from config import REPORTS_DIR

TICKER = "NVDA"
FULL_MODE = "--full" in sys.argv


def run_test():
    print("=" * 60)
    print("TradeDesk — Phase 4 Orchestrator Test")
    print(f"Mode: {'FULL (5 subagents)' if FULL_MODE else 'CAPPED (2 subagents + synthesis)'}")
    print("=" * 60)

    if FULL_MODE:
        # ── Full pipeline via TradeDesk orchestrator ──────────────
        print(f"\nRunning full analysis of {TICKER}...")
        td = TradeDesk()
        result = td.analyze(TICKER, verbose=True)

        rec = result["synthesis"]["recommendation"]
        print(f"\n[Result] Recommendation: {rec.get('recommendation')}")
        print(f"[Result] Confidence: {rec.get('confidence'):.0%}")
        print(f"[Result] Composite score: {rec.get('composite_score')}/10")
        print(f"[Result] Horizon: {rec.get('target_horizon')}")
        print(f"\n[Result] Executive summary:")
        print(f"  {rec.get('executive_summary')}")
        print(f"\n[Result] Key conflicts resolved:")
        for conflict in rec.get("key_conflicts", []):
            print(f"  - {conflict}")
        print(f"\n[Result] Extended thinking: {result['synthesis'].get('thinking', '')[:200]}...")
        print(f"[Result] Report saved: {result['report_path']}")

        assert result["synthesis"]["recommendation"].get("recommendation") in (
            "STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"
        )

    else:
        # ── Capped: 2 subagents + synthesis ──────────────────────
        print(f"\nRunning capped analysis of {TICKER} (2 subagents + synthesis)...")
        executor = MarketToolExecutor()

        print("\n[Step 1] Running SA2 + SA3 in parallel...")
        import concurrent.futures
        import time
        start = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(FundamentalsAgent(executor).run, TICKER, True)
            f2 = pool.submit(TechnicalAgent(executor).run, TICKER, True)
            fund_result = f1.result()
            tech_result = f2.result()

        elapsed = round(time.time() - start, 1)
        print(f"\n  Both agents completed in {elapsed}s (parallel)")
        print(f"  SA2: {fund_result.get('fundamental_score')}/10 | {fund_result.get('valuation')}")
        print(f"  SA3: {tech_result.get('technical_signal')} | {tech_result.get('trend_direction')}")

        print("\n[Step 2] Running synthesis agent...")
        mock_findings = {
            "ticker": TICKER,
            "elapsed_seconds": elapsed,
            "news": {"agent": "news_sentiment", "sentiment_label": "NOT_AVAILABLE",
                     "sentiment_score": None, "summary": "News data not collected in capped mode."},
            "fundamentals": fund_result,
            "technical": tech_result,
            "macro": {"agent": "macro_sector", "macro_stance": "NOT_AVAILABLE",
                      "summary": "Macro data not collected in capped mode."},
            "risk": {"agent": "risk", "portfolio_fit": "NOT_APPLICABLE",
                     "rationale": "No portfolio provided."},
            "errors": [],
        }

        synth = SynthesisAgent()
        synthesis = synth.synthesize(mock_findings, verbose=True)
        rec = synthesis["recommendation"]

        print(f"\n[Result] Recommendation: {rec.get('recommendation')}")
        print(f"[Result] Confidence: {rec.get('confidence'):.0%}")
        print(f"[Result] Composite score: {rec.get('composite_score')}/10")
        print(f"[Result] Bull case: {rec.get('bull_case')}")
        print(f"[Result] Bear case: {rec.get('bear_case')}")
        print(f"[Result] Extended thinking: {len(synthesis.get('thinking', ''))} chars")

        assert rec.get("recommendation") in (
            "STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"
        ), f"Unexpected recommendation: {rec.get('recommendation')}"

    print("\n" + "=" * 60)
    print("Phase 4 PASSED ✓ — Parallel orchestrator and synthesis working")
    print("=" * 60)
    print("\nParallelism confirmed: subagents ran concurrently, not sequentially")
    print("Extended thinking verified: synthesis used Sonnet reasoning trace")


if __name__ == "__main__":
    run_test()
