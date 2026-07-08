"""
parallel_runner.py
───────────────────
Spawns all 5 subagents concurrently using ThreadPoolExecutor.
Fixed to pre-import all dependencies before thread spawning to
avoid module resolution failures in Streamlit's thread environment.
"""

import time
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from concurrent.futures import ThreadPoolExecutor, as_completed

from src.mcp_server.market_tools import MarketToolExecutor
from src.subagents.news_agent import NewsAgent
from src.subagents.fundamentals_agent import FundamentalsAgent
from src.subagents.technical_agent import TechnicalAgent
from src.subagents.macro_agent import MacroAgent
from src.subagents.risk_agent import RiskAgent


def run_subagents_parallel(
    ticker: str,
    company_name: str = "",
    portfolio: dict = None,
    verbose: bool = True,
    status_callback=None,
) -> dict:

    executor = MarketToolExecutor()

    def _update(agent_name, status):
        if verbose:
            print(f"  [Orchestrator] {agent_name}: {status}")
        if status_callback:
            try:
                status_callback(agent_name, status)
            except Exception:
                pass

    def run_news():
        _update("SA1 NewsAgent", "running...")
        try:
            result = NewsAgent(executor).run(ticker, company_name=company_name, verbose=verbose)
            _update("SA1 NewsAgent", f"✓ {result.get('sentiment_label', 'done')}")
            return "news", result
        except Exception as e:
            _update("SA1 NewsAgent", f"✗ {str(e)[:60]}")
            return "news", {"agent": "news_sentiment", "error": str(e),
                            "sentiment_label": "NOT_AVAILABLE", "sentiment_score": None}

    def run_fundamentals():
        _update("SA2 FundamentalsAgent", "running...")
        try:
            result = FundamentalsAgent(executor).run(ticker, verbose=verbose)
            _update("SA2 FundamentalsAgent", f"✓ score {result.get('fundamental_score')}/10")
            return "fundamentals", result
        except Exception as e:
            _update("SA2 FundamentalsAgent", f"✗ {str(e)[:60]}")
            return "fundamentals", {"agent": "fundamentals", "error": str(e),
                                    "fundamental_score": None, "valuation": "NOT_AVAILABLE"}

    def run_technical():
        _update("SA3 TechnicalAgent", "running...")
        try:
            result = TechnicalAgent(executor).run(ticker, verbose=verbose)
            _update("SA3 TechnicalAgent", f"✓ {result.get('technical_signal', 'done')}")
            return "technical", result
        except Exception as e:
            _update("SA3 TechnicalAgent", f"✗ {str(e)[:60]}")
            return "technical", {"agent": "technical", "error": str(e),
                                 "technical_signal": "NOT_AVAILABLE"}

    def run_macro():
        _update("SA4 MacroAgent", "running...")
        try:
            result = MacroAgent(executor).run(ticker, company_name=company_name, verbose=verbose)
            _update("SA4 MacroAgent", f"✓ {result.get('macro_stance', 'done')}")
            return "macro", result
        except Exception as e:
            _update("SA4 MacroAgent", f"✗ {str(e)[:60]}")
            return "macro", {"agent": "macro_sector", "error": str(e),
                             "macro_stance": "NOT_AVAILABLE"}

    def run_risk():
        _update("SA5 RiskAgent", "running...")
        try:
            result = RiskAgent(executor).run(ticker, portfolio=portfolio or {}, verbose=verbose)
            _update("SA5 RiskAgent", f"✓ {result.get('portfolio_fit', 'done')}")
            return "risk", result
        except Exception as e:
            _update("SA5 RiskAgent", f"✗ {str(e)[:60]}")
            return "risk", {"agent": "risk", "error": str(e),
                            "portfolio_fit": "NOT_AVAILABLE"}

    tasks = [run_news, run_fundamentals, run_technical, run_macro, run_risk]

    if verbose:
        print(f"\n  [Orchestrator] Launching {len(tasks)} subagents in parallel for {ticker}...")

    start = time.time()
    findings = {}
    errors = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(task): task.__name__ for task in tasks}
        for future in as_completed(futures):
            try:
                key, result = future.result(timeout=120)
                findings[key] = result
                if result.get("error"):
                    errors.append(f"{key}: {result['error']}")
            except Exception as e:
                task_name = futures[future]
                errors.append(f"thread error in {task_name}: {str(e)}")

    elapsed = round(time.time() - start, 1)

    if verbose:
        print(f"\n  [Orchestrator] All subagents complete in {elapsed}s")
        if errors:
            print(f"  [Orchestrator] ⚠ {len(errors)} error(s): {errors}")

    return {
        "ticker":           ticker,
        "elapsed_seconds":  elapsed,
        "news":             findings.get("news", {}),
        "fundamentals":     findings.get("fundamentals", {}),
        "technical":        findings.get("technical", {}),
        "macro":            findings.get("macro", {}),
        "risk":             findings.get("risk", {}),
        "errors":           errors,
    }
