"""
tradedesk_orchestrator.py
──────────────────────────
The top-level TradeDesk orchestrator. Coordinates the full pipeline:
  1. Fetch company metadata
  2. Run 5 subagents in parallel
  3. Synthesize with extended thinking
  4. Save report to disk
"""

import json
import os
from datetime import datetime
from src.orchestrator.parallel_runner import run_subagents_parallel
from src.orchestrator.synthesis_agent import SynthesisAgent
from src.data.market_data import get_fundamentals, get_price_history
from config import REPORTS_DIR, DEMO_PORTFOLIO


class TradeDesk:
    """
    Top-level orchestrator for TradeDesk equity research system.

    Single stock mode:   TradeDesk().analyze("NVDA")
    Portfolio mode:      TradeDesk().analyze_portfolio({"NVDA": 0.35, ...})
    """

    def __init__(self):
        self.synthesis_agent = SynthesisAgent()
        os.makedirs(REPORTS_DIR, exist_ok=True)

    def analyze(self, ticker: str, portfolio: dict = None,
                verbose: bool = True, status_callback=None) -> dict:
        """
        Run a full single-stock analysis.

        Args:
            ticker:          Stock ticker e.g. "NVDA"
            portfolio:       Optional portfolio for SA5 context
            verbose:         Print progress to console
            status_callback: Optional callable for Streamlit UI updates

        Returns:
            Complete analysis dict including subagent findings,
            synthesis recommendation, and saved report path.
        """
        ticker = ticker.upper().strip()

        if verbose:
            print(f"\n{'='*60}")
            print(f"  TradeDesk — Analyzing {ticker}")
            print(f"{'='*60}")

        # ── Step 1: Fetch company metadata ────────────────────────
        if verbose:
            print(f"\n  [TradeDesk] Fetching company metadata...")
        fund_meta = get_fundamentals(ticker)
        company_name = fund_meta.get("company_name", ticker)
        sector = fund_meta.get("sector", "Unknown")

        if verbose:
            print(f"  [TradeDesk] {company_name} | {sector}")

        # ── Step 2: Parallel subagents ────────────────────────────
        if verbose:
            print(f"\n  [TradeDesk] Step 1/2: Running parallel subagents...")

        subagent_findings = run_subagents_parallel(
            ticker=ticker,
            company_name=company_name,
            portfolio=portfolio,
            verbose=verbose,
            status_callback=status_callback,
        )

        # ── Step 3: Synthesis ─────────────────────────────────────
        if verbose:
            print(f"\n  [TradeDesk] Step 2/2: Synthesizing with extended thinking...")

        synthesis = self.synthesis_agent.synthesize(subagent_findings, verbose=verbose)

        # ── Step 4: Build and save full report ────────────────────
        report = self._build_report(ticker, company_name, sector,
                                    subagent_findings, synthesis)
        report_path = self._save_report(report)

        if verbose:
            rec = synthesis["recommendation"].get("recommendation", "UNKNOWN")
            conf = synthesis["recommendation"].get("confidence", 0)
            print(f"\n  [TradeDesk] Complete: {ticker} → {rec} ({conf:.0%} confidence)")
            print(f"  [TradeDesk] Report saved: {report_path}")

        return {
            "ticker": ticker,
            "company_name": company_name,
            "sector": sector,
            "subagent_findings": subagent_findings,
            "synthesis": synthesis,
            "report": report,
            "report_path": report_path,
        }

    def analyze_portfolio(self, portfolio: dict = None,
                          verbose: bool = True,
                          status_callback=None) -> dict:
        """
        Analyze every stock in a portfolio with portfolio-aware context.

        Each stock gets its own full subagent run with SA5 using the
        full portfolio as context for concentration/fit analysis.
        """
        portfolio = portfolio or DEMO_PORTFOLIO
        results = {}

        if verbose:
            print(f"\n{'='*60}")
            print(f"  TradeDesk — Portfolio Analysis ({len(portfolio)} holdings)")
            print(f"{'='*60}")

        for ticker, weight in portfolio.items():
            if verbose:
                print(f"\n  Analyzing {ticker} ({weight:.0%} weight)...")
            results[ticker] = self.analyze(
                ticker=ticker,
                portfolio=portfolio,
                verbose=verbose,
                status_callback=status_callback,
            )

        # Portfolio-level summary
        recommendations = {
            t: r["synthesis"]["recommendation"].get("recommendation", "HOLD")
            for t, r in results.items()
        }
        avg_score = sum(
            r["synthesis"]["recommendation"].get("composite_score", 5.0)
            for r in results.values()
        ) / len(results)

        return {
            "portfolio": portfolio,
            "individual_results": results,
            "recommendations": recommendations,
            "portfolio_avg_score": round(avg_score, 2),
        }

    def _build_report(self, ticker, company_name, sector,
                      subagent_findings, synthesis) -> dict:
        """Assemble the complete report structure."""
        rec = synthesis["recommendation"]
        return {
            "report_id": f"TD-{ticker}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "generated_at": synthesis["generated_at"],
            "ticker": ticker,
            "company_name": company_name,
            "sector": sector,
            "recommendation": rec.get("recommendation"),
            "confidence": rec.get("confidence"),
            "composite_score": rec.get("composite_score"),
            "target_horizon": rec.get("target_horizon"),
            "signal_summary": rec.get("signal_summary", {}),
            "key_conflicts": rec.get("key_conflicts", []),
            "bull_case": rec.get("bull_case"),
            "bear_case": rec.get("bear_case"),
            "key_risks": rec.get("key_risks", []),
            "catalysts_to_watch": rec.get("catalysts_to_watch", []),
            "executive_summary": rec.get("executive_summary"),
            "reasoning": rec.get("reasoning"),
            "extended_thinking_chars": len(synthesis.get("thinking", "")),
            "parallel_run_elapsed_seconds": subagent_findings.get("elapsed_seconds"),
            "subagent_errors": subagent_findings.get("errors", []),
            "subagent_detail": {
                "news":         subagent_findings.get("news", {}),
                "fundamentals": subagent_findings.get("fundamentals", {}),
                "technical":    subagent_findings.get("technical", {}),
                "macro":        subagent_findings.get("macro", {}),
                "risk":         subagent_findings.get("risk", {}),
            },
        }

    def _save_report(self, report: dict) -> str:
        """Save report as timestamped JSON file."""
        filename = f"{report['report_id']}.json"
        path = os.path.join(REPORTS_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        return path
