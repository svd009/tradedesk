"""
parallel_runner.py
───────────────────
Spawns all 5 subagents concurrently using ThreadPoolExecutor.

Why parallel execution?
  Running 5 subagents sequentially would take 5x longer — each agent
  makes 1-3 API calls, so a full analysis could take 60+ seconds
  sequentially. Running them in parallel brings that down to the time
  of the slowest single agent (~15-20 seconds).

  This is the key architectural difference between agent CHAINING
  (FinGuard, ReconcileAgent) and true SUBAGENT DELEGATION (TradeDesk):
  chaining is sequential by definition, subagents can run in parallel
  because their contexts are isolated from each other.

Why ThreadPoolExecutor instead of asyncio?
  The Anthropic SDK and yfinance are both synchronous libraries — they
  make blocking HTTP calls. True asyncio would require an async HTTP
  client throughout the stack. ThreadPoolExecutor lets us run the
  blocking calls concurrently in separate threads, achieving the same
  parallelism benefit with much less refactoring.

  In production you'd use an async HTTP client (httpx async) for
  genuine non-blocking I/O, but for a portfolio project, threads
  give us the parallelism story without the complexity cost.

Context isolation proof:
  Each subagent instantiates its own ModelClient (its own API connection)
  and its own message history list. There is no shared state between
  agents. If SA1 crashes, SA2-SA5 continue unaffected — demonstrated
  by the try/except wrapper around each agent call below.
"""

import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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
    """
    Spawn all 5 subagents concurrently and collect their findings.

    Args:
        ticker:          Stock ticker to analyze
        company_name:    Full company name (for better news searches)
        portfolio:       Optional dict of {ticker: weight} for portfolio mode
        verbose:         Print progress to console
        status_callback: Optional callable(agent_name, status) for Streamlit UI updates

    Returns:
        {
          "ticker": str,
          "elapsed_seconds": float,
          "news": dict,
          "fundamentals": dict,
          "technical": dict,
          "macro": dict,
          "risk": dict,
          "errors": list of any agent-level errors,
        }
    """
    executor = MarketToolExecutor()

    def _update(agent_name, status):
        if verbose:
            print(f"  [Orchestrator] {agent_name}: {status}")
        if status_callback:
            status_callback(agent_name, status)

    # Define each subagent task as a (name, callable) pair
    def run_news():
        _update("SA1 NewsAgent", "running...")
        try:
            result = NewsAgent(executor).run(ticker, company_name=company_name, verbose=verbose)
            _update("SA1 NewsAgent", f"✓ {result.get('sentiment_label', 'done')}")
            return "news", result
        except Exception as e:
            _update("SA1 NewsAgent", f"✗ error: {str(e)[:50]}")
            return "news", {"agent": "news_sentiment", "error": str(e)}

    def run_fundamentals():
        _update("SA2 FundamentalsAgent", "running...")
        try:
            result = FundamentalsAgent(executor).run(ticker, verbose=verbose)
            _update("SA2 FundamentalsAgent", f"✓ score {result.get('fundamental_score')}/10")
            return "fundamentals", result
        except Exception as e:
            _update("SA2 FundamentalsAgent", f"✗ error: {str(e)[:50]}")
            return "fundamentals", {"agent": "fundamentals", "error": str(e)}

    def run_technical():
        _update("SA3 TechnicalAgent", "running...")
        try:
            result = TechnicalAgent(executor).run(ticker, verbose=verbose)
            _update("SA3 TechnicalAgent", f"✓ {result.get('technical_signal', 'done')}")
            return "technical", result
        except Exception as e:
            _update("SA3 TechnicalAgent", f"✗ error: {str(e)[:50]}")
            return "technical", {"agent": "technical", "error": str(e)}

    def run_macro():
        _update("SA4 MacroAgent", "running...")
        try:
            result = MacroAgent(executor).run(ticker, company_name=company_name, verbose=verbose)
            _update("SA4 MacroAgent", f"✓ {result.get('macro_stance', 'done')}")
            return "macro", result
        except Exception as e:
            _update("SA4 MacroAgent", f"✗ error: {str(e)[:50]}")
            return "macro", {"agent": "macro_sector", "error": str(e)}

    def run_risk():
        _update("SA5 RiskAgent", "running...")
        try:
            result = RiskAgent(executor).run(ticker, portfolio=portfolio or {}, verbose=verbose)
            _update("SA5 RiskAgent", f"✓ {result.get('portfolio_fit', 'done')}")
            return "risk", result
        except Exception as e:
            _update("SA5 RiskAgent", f"✗ error: {str(e)[:50]}")
            return "risk", {"agent": "risk", "error": str(e)}

    tasks = [run_news, run_fundamentals, run_technical, run_macro, run_risk]

    if verbose:
        print(f"\n  [Orchestrator] Launching {len(tasks)} subagents in parallel for {ticker}...")

    start = time.time()
    findings = {}
    errors = []

    # Run all agents concurrently — max_workers=5 means all start simultaneously
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(task): task.__name__ for task in tasks}
        for future in as_completed(futures):
            try:
                key, result = future.result()
                findings[key] = result
                if result.get("error"):
                    errors.append(f"{key}: {result['error']}")
            except Exception as e:
                errors.append(f"thread error: {str(e)}")

    elapsed = round(time.time() - start, 1)

    if verbose:
        print(f"\n  [Orchestrator] All subagents complete in {elapsed}s")
        if errors:
            print(f"  [Orchestrator] ⚠ {len(errors)} agent error(s): {errors}")

    return {
        "ticker": ticker,
        "elapsed_seconds": elapsed,
        "news":         findings.get("news", {}),
        "fundamentals": findings.get("fundamentals", {}),
        "technical":    findings.get("technical", {}),
        "macro":        findings.get("macro", {}),
        "risk":         findings.get("risk", {}),
        "errors":       errors,
    }
