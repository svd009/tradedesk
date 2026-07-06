"""
synthesis_agent.py
───────────────────
The synthesis agent — receives all 5 subagent findings and produces
a final investment recommendation using Claude Sonnet with extended
thinking.

Why extended thinking specifically here?
  The synthesis task is fundamentally about resolving conflicting signals.
  In the Phase 3 test, SA2 (Fundamentals) scored NVDA 8.5/10 with
  ACCELERATING growth while SA3 (Technical) signaled SELL with DOWNTREND.
  Those two signals directly contradict each other.

  Without extended thinking, a model tends to produce surface-level
  synthesis — picking one signal over another or averaging them.
  With extended thinking, Claude gets a private reasoning scratchpad
  to work through WHY signals conflict, WHICH should take precedence
  given the investment time horizon, and HOW confident it should be
  in the final recommendation given the disagreement.

  This is where TradeDesk is architecturally stronger than a single
  "analyze this stock" prompt — each subagent brings independent,
  domain-specific evidence, and the synthesis agent reasons over the
  combination rather than just the surface-level question.

What happens with None values from SA1/SA4?
  The prompt explicitly instructs the synthesis agent to treat None/missing
  values as "data not available" and adjust confidence accordingly rather
  than crashing or hallucinating. This handles the web search null issue
  we saw in Phase 3 testing.
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.client.model_client import ModelClient
from datetime import datetime


SYNTHESIS_SYSTEM_PROMPT = """You are a senior equity research analyst and portfolio manager with 20 years of experience at a top-tier investment firm.

You receive structured findings from 5 specialized research subagents and synthesize them into a single, actionable investment recommendation.

<your_task>
Synthesize the subagent findings into a final recommendation. When signals conflict, reason through WHY they conflict and which should take precedence. Do not simply average signals — apply genuine analytical judgment.
</your_task>

<conflict_resolution_framework>
FUNDAMENTAL vs TECHNICAL conflict:
  - Strong fundamentals + bearish technicals → usually HOLD or scale-in slowly
  - Weak fundamentals + bullish technicals → usually HOLD, momentum may be unsustainable
  - Both agree → higher conviction recommendation

NEWS vs FUNDAMENTAL conflict:
  - Bad news + good fundamentals → assess if news is temporary or structural
  - Good news + weak fundamentals → be cautious of narrative-driven price action

MACRO vs EVERYTHING conflict:
  - Adverse macro can override strong single-stock fundamentals
  - Favorable macro amplifies already-positive signals

MISSING DATA:
  - If any subagent returned null/None values, treat that dimension as "data not available"
  - Reduce overall confidence proportionally to missing data
  - Do not hallucinate or assume values for missing data
</conflict_resolution_framework>

<output_format>
Return ONLY valid JSON with no markdown fences:
{
  "ticker": "string",
  "recommendation": "STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL",
  "confidence": float between 0.0 and 1.0,
  "target_horizon": "SHORT_TERM (1-3 months) | MEDIUM_TERM (3-12 months) | LONG_TERM (1+ years)",
  "composite_score": float between 1.0 and 10.0,
  "signal_summary": {
    "news_sentiment": "BULLISH | NEUTRAL | BEARISH | NOT_AVAILABLE",
    "fundamental_health": "STRONG | ADEQUATE | WEAK | NOT_AVAILABLE",
    "technical_signal": "BULLISH | NEUTRAL | BEARISH | NOT_AVAILABLE",
    "macro_environment": "FAVORABLE | NEUTRAL | UNFAVORABLE | NOT_AVAILABLE",
    "portfolio_fit": "GOOD | NEUTRAL | POOR | NOT_APPLICABLE"
  },
  "key_conflicts": ["describe any major signal conflicts and how you resolved them"],
  "bull_case": "2-3 sentence bull case for the stock",
  "bear_case": "2-3 sentence bear case for the stock",
  "key_risks": ["top 3 risks to the recommendation"],
  "catalysts_to_watch": ["top 2-3 upcoming events or data points to monitor"],
  "executive_summary": "3-4 sentence plain English summary suitable for a portfolio manager",
  "reasoning": "Detailed explanation of how you weighed the signals and reached this recommendation"
}
</output_format>"""


class SynthesisAgent:
    """
    Final synthesis agent using Claude Sonnet with extended thinking.
    Receives all 5 subagent findings and produces the investment recommendation.
    """

    def __init__(self):
        self.client = ModelClient()

    def synthesize(self, subagent_findings: dict, verbose: bool = True) -> dict:
        """
        Synthesize 5 subagent findings into a final recommendation.

        Args:
            subagent_findings: Output from parallel_runner.run_subagents_parallel()
            verbose:           Print progress to console

        Returns:
            {
              "recommendation": dict,   ← parsed final recommendation
              "raw_response": str,      ← Claude's raw JSON output
              "thinking": str,          ← extended thinking trace
              "ticker": str,
              "generated_at": str,
            }
        """
        ticker = subagent_findings.get("ticker", "UNKNOWN")

        if verbose:
            print(f"\n  [SynthesisAgent] Synthesizing findings for {ticker}...")
            print(f"  [SynthesisAgent] Model: Sonnet + extended thinking")

        # Build a clean summary of subagent findings for the prompt
        findings_summary = self._format_findings(subagent_findings)

        user_message = f"""
<ticker>{ticker}</ticker>

<subagent_findings>
{findings_summary}
</subagent_findings>

<task>
Synthesize these 5 research streams into a final investment recommendation.
Apply your conflict resolution framework where signals disagree.
Note which dimensions have missing or null data and adjust confidence accordingly.
Return structured JSON exactly as specified in your system prompt.
No markdown fences. JSON only.
</task>
"""
        messages = [{"role": "user", "content": user_message}]
        thinking_text = ""

        response = self.client.create_message(
            model="reasoning",
            messages=messages,
            system=SYNTHESIS_SYSTEM_PROMPT,
            use_thinking=True,
        )

        # Extract thinking and text blocks
        for block in response.content:
            if block.type == "thinking":
                thinking_text += block.thinking
                if verbose:
                    print(f"  [SynthesisAgent] Extended thinking: {len(block.thinking)} chars")

        raw_text = "".join(
            block.text for block in response.content
            if hasattr(block, "text")
        )

        recommendation = self._parse_recommendation(raw_text)

        if verbose:
            rec = recommendation.get("recommendation", "UNKNOWN")
            conf = recommendation.get("confidence", 0)
            score = recommendation.get("composite_score", 0)
            print(f"  [SynthesisAgent] ✓ {rec} | confidence: {conf:.0%} | score: {score}/10")

        return {
            "recommendation": recommendation,
            "raw_response": raw_text,
            "thinking": thinking_text,
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(),
        }

    def _format_findings(self, findings: dict) -> str:
        """Format subagent findings as clean JSON for the synthesis prompt."""
        # Exclude raw arrays and verbose fields that bloat the context
        def clean(d: dict) -> dict:
            if not isinstance(d, dict):
                return d
            exclude = {"raw_output", "dates", "closes", "volumes", "raw_filings"}
            return {k: v for k, v in d.items() if k not in exclude}

        summary = {
            "SA1_news_sentiment":    clean(findings.get("news", {})),
            "SA2_fundamentals":      clean(findings.get("fundamentals", {})),
            "SA3_technical":         clean(findings.get("technical", {})),
            "SA4_macro_sector":      clean(findings.get("macro", {})),
            "SA5_portfolio_risk":    clean(findings.get("risk", {})),
            "parallel_run_elapsed":  findings.get("elapsed_seconds"),
            "agent_errors":          findings.get("errors", []),
        }
        return json.dumps(summary, indent=2, default=str)

    def _parse_recommendation(self, raw: str) -> dict:
        """Parse the synthesis agent's JSON output safely."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {
                "recommendation": "HOLD",
                "confidence": 0.0,
                "composite_score": 5.0,
                "executive_summary": "JSON parsing failed — see raw_response for full output.",
                "parse_error": True,
                "raw": raw[:500],
            }
