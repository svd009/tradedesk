"""
parallel_runner.py
───────────────────
Spawns all 5 subagents concurrently using ThreadPoolExecutor.
Fixed to pre-import all dependencies before thread spawning to
avoid module resolution failures in Streamlit's thread environment.

Timing note: every subagent's wall-clock time is now measured and
returned, regardless of the `verbose` flag. Previously only visible
when verbose=True (which app.py doesn't set), so slow runs had no
way to be diagnosed. Look at result["agent_timings"] or the printed
breakdown to see which specific subagent is the actual bottleneck.

Bounded time budget: the whole parallel batch has a fixed time limit
(SUBAGENT_BATCH_TIME_BUDGET_SECONDS). Whichever subagents haven't
finished by then are marked unavailable rather than making every user
wait on the single slowest one — the synthesis agent already knows how
to produce a reasonable answer with some signals missing. Honest caveat:
ThreadPoolExecutor can't truly cancel a thread that's already running,
so a timed-out subagent's work keeps executing in the background until
it finishes on its own; this budget only stops the app from *waiting*
on it, it doesn't free the underlying resources early.
"""

import time
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

from config import SUBAGENT_BATCH_TIME_BUDGET_SECONDS
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
    agent_timings = {}

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
        t0 = time.time()
        try:
            result = NewsAgent(executor).run(ticker, company_name=company_name, verbose=verbose)
            elapsed = round(time.time() - t0, 1)
            agent_timings["news"] = elapsed
            result["_elapsed_seconds"] = elapsed
            _update("SA1 NewsAgent", f"✓ {result.get('sentiment_label', 'done')} ({elapsed}s)")
            return "news", result
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            agent_timings["news"] = elapsed
            _update("SA1 NewsAgent", f"✗ {str(e)[:60]} ({elapsed}s)")
            return "news", {"agent": "news_sentiment", "error": str(e),
                            "sentiment_label": "NOT_AVAILABLE", "sentiment_score": None,
                            "_elapsed_seconds": elapsed}

    def run_fundamentals():
        _update("SA2 FundamentalsAgent", "running...")
        t0 = time.time()
        try:
            result = FundamentalsAgent(executor).run(ticker, verbose=verbose)
            elapsed = round(time.time() - t0, 1)
            agent_timings["fundamentals"] = elapsed
            result["_elapsed_seconds"] = elapsed
            _update("SA2 FundamentalsAgent", f"✓ score {result.get('fundamental_score')}/10 ({elapsed}s)")
            return "fundamentals", result
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            agent_timings["fundamentals"] = elapsed
            _update("SA2 FundamentalsAgent", f"✗ {str(e)[:60]} ({elapsed}s)")
            return "fundamentals", {"agent": "fundamentals", "error": str(e),
                                    "fundamental_score": None, "valuation": "NOT_AVAILABLE",
                                    "_elapsed_seconds": elapsed}

    def run_technical():
        _update("SA3 TechnicalAgent", "running...")
        t0 = time.time()
        try:
            result = TechnicalAgent(executor).run(ticker, verbose=verbose)
            elapsed = round(time.time() - t0, 1)
            agent_timings["technical"] = elapsed
            result["_elapsed_seconds"] = elapsed
            _update("SA3 TechnicalAgent", f"✓ {result.get('technical_signal', 'done')} ({elapsed}s)")
            return "technical", result
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            agent_timings["technical"] = elapsed
            _update("SA3 TechnicalAgent", f"✗ {str(e)[:60]} ({elapsed}s)")
            return "technical", {"agent": "technical", "error": str(e),
                                 "technical_signal": "NOT_AVAILABLE",
                                 "_elapsed_seconds": elapsed}

    def run_macro():
        _update("SA4 MacroAgent", "running...")
        t0 = time.time()
        try:
            result = MacroAgent(executor).run(ticker, company_name=company_name, verbose=verbose)
            elapsed = round(time.time() - t0, 1)
            agent_timings["macro"] = elapsed
            result["_elapsed_seconds"] = elapsed
            _update("SA4 MacroAgent", f"✓ {result.get('macro_stance', 'done')} ({elapsed}s)")
            return "macro", result
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            agent_timings["macro"] = elapsed
            _update("SA4 MacroAgent", f"✗ {str(e)[:60]} ({elapsed}s)")
            return "macro", {"agent": "macro_sector", "error": str(e),
                             "macro_stance": "NOT_AVAILABLE",
                             "_elapsed_seconds": elapsed}

    def run_risk():
        _update("SA5 RiskAgent", "running...")
        t0 = time.time()
        try:
            result = RiskAgent(executor).run(ticker, portfolio=portfolio or {}, verbose=verbose)
            elapsed = round(time.time() - t0, 1)
            agent_timings["risk"] = elapsed
            result["_elapsed_seconds"] = elapsed
            _update("SA5 RiskAgent", f"✓ {result.get('portfolio_fit', 'done')} ({elapsed}s)")
            return "risk", result
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            agent_timings["risk"] = elapsed
            _update("SA5 RiskAgent", f"✗ {str(e)[:60]} ({elapsed}s)")
            return "risk", {"agent": "risk", "error": str(e),
                            "portfolio_fit": "NOT_AVAILABLE",
                            "_elapsed_seconds": elapsed}

    tasks = [run_news, run_fundamentals, run_technical, run_macro, run_risk]

    if verbose:
        print(f"\n  [Orchestrator] Launching {len(tasks)} subagents in parallel for {ticker}...")

    start = time.time()
    findings = {}
    errors = []

    pool = ThreadPoolExecutor(max_workers=5)
    futures = {pool.submit(task): task.__name__ for task in tasks}
    done, not_done = wait(
        futures.keys(),
        timeout=SUBAGENT_BATCH_TIME_BUDGET_SECONDS,
        return_when=ALL_COMPLETED,
    )

    for future in done:
        try:
            key, result = future.result()
            findings[key] = result
            if result.get("error"):
                errors.append(f"{key}: {result['error']}")
        except Exception as e:
            task_name = futures[future]
            errors.append(f"thread error in {task_name}: {str(e)}")

    for future in not_done:
        # Ran past the batch's time budget. We stop WAITING on it (the
        # thread itself keeps running in the background until it
        # finishes on its own — ThreadPoolExecutor can't preempt a
        # running thread), and mark this signal unavailable so the
        # rest of the analysis isn't held hostage by the slowest one.
        task_name = futures[future]
        agent_key = task_name.replace("run_", "")
        findings[agent_key] = {
            "agent": agent_key,
            "error": f"Exceeded the {SUBAGENT_BATCH_TIME_BUDGET_SECONDS}s time budget for this batch",
            "_timed_out": True,
        }
        errors.append(f"{agent_key}: exceeded {SUBAGENT_BATCH_TIME_BUDGET_SECONDS}s time budget")

    # wait=False: don't block returning to the caller on any straggler
    # thread finishing — that's the whole point of the time budget above.
    # The straggler(s) keep running in the background and are simply
    # abandoned; Python's garbage collector cleans them up once they
    # finish naturally, since nothing holds a reference to their futures
    # after this function returns.
    pool.shutdown(wait=False)

    elapsed = round(time.time() - start, 1)

    # Always print the per-agent timing breakdown (not gated by verbose) —
    # this is the one piece of diagnostic info you need in Streamlit Cloud
    # logs to see which specific subagent is actually the slow one.
    breakdown = ", ".join(f"{k}={v}s" for k, v in sorted(agent_timings.items(), key=lambda x: -x[1]))
    print(f"  [Orchestrator] Subagents wall time: {elapsed}s total (parallel) — {breakdown}")
    if errors:
        print(f"  [Orchestrator] ⚠ {len(errors)} error(s): {errors}")

    return {
        "ticker":           ticker,
        "elapsed_seconds":  elapsed,
        "agent_timings":    agent_timings,
        "news":             findings.get("news", {}),
        "fundamentals":     findings.get("fundamentals", {}),
        "technical":        findings.get("technical", {}),
        "macro":            findings.get("macro", {}),
        "risk":             findings.get("risk", {}),
        "errors":           errors,
    }
