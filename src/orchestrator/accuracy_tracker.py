"""
accuracy_tracker.py
─────────────────────
Turns the historical record in storage.py into an actual, checkable
track record: for every recommendation old enough to judge, fetch the
real current price and check whether the direction implied by the
recommendation (BUY/SELL/HOLD) actually happened.

This is deliberately a simple, transparent heuristic, not a rigorous
quantitative backtest:
  - BUY / STRONG_BUY   correct if price is now more than +2% higher
  - SELL / STRONG_SELL correct if price is now more than -2% lower
  - HOLD               correct if price stayed within +/-5%
  - Anything that doesn't clearly satisfy or contradict the call counts
    as inconclusive, rather than being forced into right/wrong.

A real quantitative backtest would weight by holding period, compare
against the broader market's move over the same window (a stock up 3%
during a 10% market rally is a very different result from up 3% in a
flat market), and control for a dozen other things. This is intentionally
simpler than that: an honest, transparent "were we directionally right"
signal, built to be understandable at a glance, not a Sharpe ratio.

Why a fixed minimum age before checking? A recommendation looked at one
hour after it was made hasn't had any time to be right or wrong yet.
The default (7 days) gives every recommendation at least a trading week
to play out before it's judged.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.orchestrator import storage
from src.data.market_data import get_price_history

_ET = ZoneInfo("America/New_York")
DEFAULT_MIN_AGE_DAYS = 7

_BUY_LABELS = {"BUY", "STRONG_BUY"}
_SELL_LABELS = {"SELL", "STRONG_SELL"}
_HOLD_LABELS = {"HOLD"}

_BUY_SELL_THRESHOLD_PCT = 2.0   # BUY/SELL need at least this much move to count
_HOLD_BAND_PCT = 5.0            # HOLD is "correct" if price stayed within this band


def _classify(recommendation: str, pct_change: float) -> str:
    """Returns 'correct', 'incorrect', or 'inconclusive' for one recommendation."""
    if recommendation in _BUY_LABELS:
        if pct_change > _BUY_SELL_THRESHOLD_PCT:
            return "correct"
        if pct_change < -_BUY_SELL_THRESHOLD_PCT:
            return "incorrect"
        return "inconclusive"
    if recommendation in _SELL_LABELS:
        if pct_change < -_BUY_SELL_THRESHOLD_PCT:
            return "correct"
        if pct_change > _BUY_SELL_THRESHOLD_PCT:
            return "incorrect"
        return "inconclusive"
    if recommendation in _HOLD_LABELS:
        return "correct" if abs(pct_change) <= _HOLD_BAND_PCT else "incorrect"
    return "inconclusive"  # unrecognized recommendation label


def compute_track_record(min_age_days: int = DEFAULT_MIN_AGE_DAYS) -> dict:
    """
    Checks every stored analysis old enough to judge against its real,
    current outcome. Returns an aggregate report plus the individual
    entries checked, so the UI can show both a headline number and the
    receipts behind it.
    """
    raw_items = storage.get_all_analyses()
    now = datetime.now(_ET)
    cutoff = now - timedelta(days=min_age_days)

    checked = []
    skipped_no_baseline = 0
    skipped_too_recent = 0
    skipped_price_unavailable = 0

    for item in raw_items:
        if not item.get("baseline_price"):
            skipped_no_baseline += 1
            continue

        try:
            cached_at = datetime.fromisoformat(item["cached_at"])
        except Exception:
            continue

        if cached_at > cutoff:
            skipped_too_recent += 1
            continue

        ticker = item["ticker"]
        try:
            baseline_price = float(item["baseline_price"])
        except (TypeError, ValueError):
            skipped_no_baseline += 1
            continue

        current = get_price_history(ticker, days=5)
        if current.get("error") or not current.get("closes"):
            skipped_price_unavailable += 1
            continue

        current_price = current["closes"][-1]
        pct_change = ((current_price - baseline_price) / baseline_price) * 100
        recommendation = item.get("recommendation", "UNKNOWN")
        verdict = _classify(recommendation, pct_change)

        checked.append({
            "ticker": ticker,
            "recommendation": recommendation,
            "confidence": item.get("confidence"),
            "cached_at": item["cached_at"],
            "baseline_price": round(baseline_price, 2),
            "current_price": round(current_price, 2),
            "pct_change": round(pct_change, 2),
            "verdict": verdict,
        })

    correct = sum(1 for c in checked if c["verdict"] == "correct")
    incorrect = sum(1 for c in checked if c["verdict"] == "incorrect")
    inconclusive = sum(1 for c in checked if c["verdict"] == "inconclusive")
    judged = correct + incorrect  # inconclusive calls are excluded from
                                  # the accuracy percentage on purpose —
                                  # they were never a clear enough test either way

    return {
        "total_checked": len(checked),
        "correct": correct,
        "incorrect": incorrect,
        "inconclusive": inconclusive,
        "accuracy_pct": round(100 * correct / judged, 1) if judged else None,
        "skipped_no_baseline": skipped_no_baseline,
        "skipped_too_recent": skipped_too_recent,
        "skipped_price_unavailable": skipped_price_unavailable,
        "details": sorted(checked, key=lambda c: c["cached_at"], reverse=True),
    }
