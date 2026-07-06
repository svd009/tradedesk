"""
eval_framework.py
──────────────────
Evaluation framework for TradeDesk synthesis quality.

What we're evaluating here is different from FinGuard and ReconcileAgent:
  - FinGuard: retrieval quality (did we find the right documents?)
  - ReconcileAgent: decision accuracy (did we make the right call?)
  - TradeDesk: synthesis quality (is the recommendation well-reasoned,
    consistent, and properly grounded in the subagent evidence?)

We can't evaluate "was the stock call correct?" in real time — that
requires waiting weeks or months and comparing against actual price
movement. Instead we evaluate the QUALITY of the reasoning process:

  1. COMPLETENESS (25%)
     Did the synthesis use evidence from all available subagents?
     Did it acknowledge missing data rather than ignoring it?

  2. CONSISTENCY (25%)
     Do the signal_summary labels match what the subagents actually said?
     Is the recommendation consistent with the composite_score?
     (e.g. a BUY with composite_score 3.5 would be inconsistent)

  3. CONFLICT RESOLUTION (25%)
     When subagents disagreed, did the synthesis acknowledge and address
     the conflict explicitly rather than silently ignoring one signal?

  4. STRUCTURE (25%)
     Does the output have all required fields?
     Are confidence scores within valid ranges?
     Is the executive_summary substantive?

No API calls needed — this is pure structural and logical validation.
"""

from config import EVAL_PASS_THRESHOLD


class TradeDeskevaluator:
    """
    Evaluates the quality of a TradeDesk synthesis recommendation.
    """

    def evaluate(self, synthesis_result: dict, subagent_findings: dict,
                 verbose: bool = True) -> dict:
        """
        Score a synthesis recommendation across 4 quality dimensions.

        Args:
            synthesis_result:  Output from SynthesisAgent.synthesize()
            subagent_findings: Output from run_subagents_parallel()
            verbose:           Print scoring details

        Returns:
            {
              "completeness_score": float (0-10),
              "consistency_score":  float (0-10),
              "conflict_score":     float (0-10),
              "structure_score":    float (0-10),
              "overall_score":      float (0-10),
              "passed":             bool,
              "details":            dict,
            }
        """
        rec = synthesis_result.get("recommendation", {})
        thinking = synthesis_result.get("thinking", "")

        if verbose:
            print(f"\n  [Evaluator] Scoring synthesis quality for "
                  f"{synthesis_result.get('ticker', 'UNKNOWN')}...")

        c1 = self._score_completeness(rec, subagent_findings)
        c2 = self._score_consistency(rec)
        c3 = self._score_conflict_resolution(rec, subagent_findings, thinking)
        c4 = self._score_structure(rec)

        overall = round((c1 + c2 + c3 + c4) / 4, 2)
        passed = overall >= EVAL_PASS_THRESHOLD

        if verbose:
            print(f"  [Evaluator] Completeness:        {c1:.1f}/10")
            print(f"  [Evaluator] Consistency:         {c2:.1f}/10")
            print(f"  [Evaluator] Conflict resolution: {c3:.1f}/10")
            print(f"  [Evaluator] Structure:           {c4:.1f}/10")
            print(f"  [Evaluator] Overall:             {overall:.1f}/10 "
                  f"({'PASSED' if passed else 'BELOW THRESHOLD'})")

        return {
            "completeness_score":  c1,
            "consistency_score":   c2,
            "conflict_score":      c3,
            "structure_score":     c4,
            "overall_score":       overall,
            "passed":              passed,
            "threshold":           EVAL_PASS_THRESHOLD,
            "details": {
                "has_thinking_trace":  len(thinking) > 100,
                "thinking_chars":      len(thinking),
                "recommendation":      rec.get("recommendation"),
                "composite_score":     rec.get("composite_score"),
                "confidence":          rec.get("confidence"),
            }
        }

    def _score_completeness(self, rec: dict, findings: dict) -> float:
        """
        Did the synthesis acknowledge all 5 research streams?
        Checks signal_summary coverage and missing data handling.
        """
        score = 10.0
        signal_summary = rec.get("signal_summary", {})

        # Each available subagent stream should appear in signal_summary
        stream_map = {
            "news":         "news_sentiment",
            "fundamentals": "fundamental_health",
            "technical":    "technical_signal",
            "macro":        "macro_environment",
            "risk":         "portfolio_fit",
        }

        missing_streams = 0
        for agent_key, summary_key in stream_map.items():
            agent_data = findings.get(agent_key, {})
            has_data = bool(agent_data and not agent_data.get("error"))
            in_summary = summary_key in signal_summary

            if has_data and not in_summary:
                # Agent produced data but synthesis ignored it
                missing_streams += 1
                score -= 2.0
            elif not has_data and summary_key in signal_summary:
                val = signal_summary[summary_key]
                if val not in ("NOT_AVAILABLE", "NOT_APPLICABLE", None):
                    # Agent had no data but synthesis didn't acknowledge it
                    score -= 1.0

        return max(0.0, score)

    def _score_consistency(self, rec: dict) -> float:
        """
        Are the recommendation and composite_score internally consistent?
        A BUY should have score >= 6.5, SELL should have score <= 4.5, etc.
        """
        score = 10.0
        recommendation = rec.get("recommendation", "")
        composite = rec.get("composite_score")
        confidence = rec.get("confidence")

        if composite is None or recommendation == "":
            return 4.0  # Missing critical fields

        # Check recommendation vs score alignment
        score_map = {
            "STRONG_BUY":  (8.0, 10.0),
            "BUY":         (6.5, 10.0),
            "HOLD":        (4.0,  7.0),
            "SELL":        (0.0,  5.5),
            "STRONG_SELL": (0.0,  4.0),
        }
        expected_range = score_map.get(recommendation)
        if expected_range:
            lo, hi = expected_range
            if not (lo <= composite <= hi):
                score -= 3.0  # Significant inconsistency

        # Check confidence is in valid range
        if confidence is not None:
            if not (0.0 <= confidence <= 1.0):
                score -= 2.0
        else:
            score -= 1.0

        # Check target_horizon is present
        if not rec.get("target_horizon"):
            score -= 1.0

        return max(0.0, score)

    def _score_conflict_resolution(self, rec: dict, findings: dict,
                                   thinking: str) -> float:
        """
        When signals conflict, did the synthesis address it explicitly?

        Detects the most obvious conflict: fundamentals say strong but
        technical says bearish (or vice versa), and checks whether
        key_conflicts is non-empty and thinking trace exists.
        """
        score = 10.0

        fund = findings.get("fundamentals", {})
        tech = findings.get("technical", {})

        fund_score = fund.get("fundamental_score", 5)
        tech_signal = tech.get("technical_signal", "NEUTRAL")

        # Check for the fundamental vs technical conflict
        fund_bullish = fund_score and fund_score >= 7.0
        tech_bearish = tech_signal in ("SELL", "STRONG_SELL")
        tech_bullish = tech_signal in ("BUY", "STRONG_BUY")
        fund_bearish = fund_score and fund_score <= 4.0

        has_conflict = (fund_bullish and tech_bearish) or (tech_bullish and fund_bearish)

        if has_conflict:
            key_conflicts = rec.get("key_conflicts", [])
            if not key_conflicts:
                score -= 4.0  # Conflict existed but wasn't addressed
            elif len(key_conflicts) == 0:
                score -= 2.0

        # Extended thinking trace is a strong signal the synthesis reasoned carefully
        if len(thinking) < 200:
            score -= 2.0  # Very short thinking trace suggests shallow reasoning
        elif len(thinking) > 1000:
            score += 0.0  # Reward thorough thinking (already at max)

        # Bull and bear case should both be present
        if not rec.get("bull_case"):
            score -= 1.5
        if not rec.get("bear_case"):
            score -= 1.5

        return max(0.0, min(10.0, score))

    def _score_structure(self, rec: dict) -> float:
        """
        Does the output have all required fields with valid values?
        """
        score = 10.0
        required_fields = [
            "recommendation", "confidence", "composite_score",
            "target_horizon", "signal_summary", "bull_case",
            "bear_case", "key_risks", "executive_summary",
        ]

        for field in required_fields:
            val = rec.get(field)
            if val is None or val == "" or val == []:
                score -= 1.0

        # Executive summary should be substantive
        summary = rec.get("executive_summary", "")
        if isinstance(summary, str) and len(summary) < 50:
            score -= 1.0

        # key_risks should have at least 1 entry
        risks = rec.get("key_risks", [])
        if not risks:
            score -= 1.0

        return max(0.0, score)
