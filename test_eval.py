"""
test_eval.py
─────────────
Smoke test for Phase 5 — evaluation framework.
Uses mock synthesis output — zero API cost.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.evaluation.eval_framework import TradeDeskevaluator


def make_mock_findings(fund_score=8.5, tech_signal="BUY"):
    return {
        "ticker": "NVDA",
        "elapsed_seconds": 18.3,
        "news":         {"agent": "news_sentiment", "sentiment_label": "BULLISH",
                         "sentiment_score": 0.7, "summary": "Strong AI demand narrative"},
        "fundamentals": {"agent": "fundamentals", "fundamental_score": fund_score,
                         "valuation": "FAIR", "growth_trajectory": "ACCELERATING"},
        "technical":    {"agent": "technical", "technical_signal": tech_signal,
                         "trend_direction": "UPTREND" if tech_signal in ("BUY","STRONG_BUY") else "DOWNTREND"},
        "macro":        {"agent": "macro_sector", "macro_stance": "FAVORABLE",
                         "sector_momentum": "STRONG"},
        "risk":         {"agent": "risk", "portfolio_fit": "NEUTRAL",
                         "concentration_risk": "MEDIUM"},
        "errors": [],
    }


def make_mock_synthesis(rec="BUY", score=7.5, confidence=0.78,
                        conflicts=None, thinking_len=500):
    return {
        "ticker": "NVDA",
        "generated_at": "2026-07-01T10:00:00",
        "thinking": "x" * thinking_len,
        "recommendation": {
            "recommendation": rec,
            "confidence": confidence,
            "composite_score": score,
            "target_horizon": "MEDIUM_TERM (3-12 months)",
            "signal_summary": {
                "news_sentiment": "BULLISH",
                "fundamental_health": "STRONG",
                "technical_signal": "BULLISH",
                "macro_environment": "FAVORABLE",
                "portfolio_fit": "NEUTRAL",
            },
            "key_conflicts": conflicts or [],
            "bull_case": "NVDA is the dominant AI chip supplier with strong pricing power.",
            "bear_case": "Valuation is stretched and competition from AMD and custom silicon is growing.",
            "key_risks": ["Valuation multiple compression", "Export restrictions", "Customer concentration"],
            "catalysts_to_watch": ["Next earnings call", "Blackwell chip demand updates"],
            "executive_summary": "NVDA presents a compelling medium-term opportunity driven by AI infrastructure spending, though near-term technical weakness and valuation concerns warrant a measured entry.",
            "reasoning": "Strong fundamentals and macro tailwinds outweigh the near-term technical weakness.",
        }
    }


def run_test():
    print("=" * 60)
    print("TradeDesk — Phase 5 Evaluation Framework Test")
    print("=" * 60)
    print("\nNOTE: Zero API cost — uses mock synthesis data")

    evaluator = TradeDeskevaluator()

    # ── Test 1: High quality synthesis ───────────────────────────
    print("\n[Test 1] High-quality synthesis (should score >= 7.0)...")
    findings = make_mock_findings(fund_score=8.5, tech_signal="BUY")
    synthesis = make_mock_synthesis(rec="BUY", score=7.5, confidence=0.78,
                                    thinking_len=800)
    result = evaluator.evaluate(synthesis, findings, verbose=True)
    assert result["overall_score"] >= 7.0, f"Expected >= 7.0, got {result['overall_score']}"
    assert result["passed"] is True
    print(f"  ✓ Score: {result['overall_score']}/10 — PASSED")

    # ── Test 2: Inconsistent recommendation vs score ───────────────
    print("\n[Test 2] Inconsistent recommendation (BUY with score 3.0)...")
    synthesis_bad = make_mock_synthesis(rec="BUY", score=3.0, confidence=0.9)
    result2 = evaluator.evaluate(synthesis_bad, findings, verbose=False)
    assert result2["consistency_score"] < 8.0, "Should penalize BUY with score 3.0"
    print(f"  ✓ Consistency score correctly penalized: {result2['consistency_score']}/10")

    # ── Test 3: Conflict not addressed ────────────────────────────
    print("\n[Test 3] Unresolved conflict (strong fundamentals + SELL technical)...")
    conflict_findings = make_mock_findings(fund_score=8.5, tech_signal="SELL")
    synthesis_no_conflict = make_mock_synthesis(rec="HOLD", score=5.5,
                                                conflicts=[],
                                                thinking_len=150)
    result3 = evaluator.evaluate(synthesis_no_conflict, conflict_findings, verbose=False)
    assert result3["conflict_score"] < 7.0, "Should penalize unresolved conflict"
    print(f"  ✓ Conflict score correctly penalized: {result3['conflict_score']}/10")

    # ── Test 4: Conflict properly addressed ───────────────────────
    print("\n[Test 4] Properly resolved conflict (same scenario, conflict addressed)...")
    synthesis_with_conflict = make_mock_synthesis(
        rec="HOLD", score=5.5,
        conflicts=["SA2 fundamentals scored 8.5/10 (strong) but SA3 signaled SELL (downtrend). Weighted fundamentals more heavily for medium-term horizon as technical weakness may be temporary."],
        thinking_len=1200
    )
    result4 = evaluator.evaluate(synthesis_with_conflict, conflict_findings, verbose=False)
    assert result4["conflict_score"] > result3["conflict_score"], \
        "Addressed conflict should score higher than unaddressed"
    print(f"  ✓ Addressed conflict scored higher: {result4['conflict_score']}/10 vs {result3['conflict_score']}/10")

    # ── Test 5: Missing fields ────────────────────────────────────
    print("\n[Test 5] Missing required fields...")
    synthesis_incomplete = {
        "ticker": "NVDA",
        "thinking": "",
        "generated_at": "2026-07-01",
        "recommendation": {
            "recommendation": "BUY",
            "confidence": 0.7,
            "composite_score": 7.0,
        }
    }
    result5 = evaluator.evaluate(synthesis_incomplete, findings, verbose=False)
    assert result5["structure_score"] < 7.0, "Incomplete output should score low on structure"
    print(f"  ✓ Incomplete output correctly penalized: {result5['structure_score']}/10")

    print("\n" + "=" * 60)
    print("Phase 5 PASSED ✓ — Evaluation framework verified")
    print("=" * 60)
    print("\nEval framework measures: completeness, consistency,")
    print("conflict resolution, and structure — not just 'did it run'.")


if __name__ == "__main__":
    run_test()
