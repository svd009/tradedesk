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


# Confidence buckets to group checked recommendations into. Each is
# (label, lower bound inclusive, upper bound exclusive except the last).
_CONFIDENCE_BUCKETS = [
    ("50-60%", 0.50, 0.60),
    ("60-70%", 0.60, 0.70),
    ("70-80%", 0.70, 0.80),
    ("80-90%", 0.80, 0.90),
    ("90-100%", 0.90, 1.01),  # 1.01 so a stated confidence of exactly 1.0 is included
]


def compute_calibration_report(min_age_days: int = DEFAULT_MIN_AGE_DAYS) -> dict:
    """
    Checks whether the model's own stated confidence is actually
    meaningful: when it says "70% confident," is it right about 70% of
    the time? Groups every judged (correct/incorrect, not inconclusive)
    recommendation by its stated confidence, then compares each
    bucket's REAL accuracy rate against what that confidence level
    implies.

    A well-calibrated model's buckets should roughly track the diagonal
    (70-80% confidence calls right roughly 70-80% of the time). A
    negative "gap" means overconfidence — the model claims more
    certainty than its track record actually supports at that level.
    A positive gap means underconfidence — it's more often right than
    it claims to be.

    This is deliberately a small, transparent bucket-based check, not
    a full statistical calibration curve (e.g. no confidence intervals
    on the gap itself) — meaningful with the sample sizes this app is
    realistically going to have for a while, and easy to explain at a
    glance, which matters more than statistical sophistication here.
    """
    track_record = compute_track_record(min_age_days)

    bucket_counts = {label: {"correct": 0, "incorrect": 0} for label, _, _ in _CONFIDENCE_BUCKETS}

    for entry in track_record["details"]:
        if entry["verdict"] not in ("correct", "incorrect"):
            continue  # inconclusive calls were never a clean test of confidence either
        conf = entry.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue

        for label, lo, hi in _CONFIDENCE_BUCKETS:
            if lo <= conf < hi:
                bucket_counts[label][entry["verdict"]] += 1
                break

    buckets = []
    for label, lo, hi in _CONFIDENCE_BUCKETS:
        counts = bucket_counts[label]
        total = counts["correct"] + counts["incorrect"]
        if total == 0:
            continue
        actual_accuracy = round(100 * counts["correct"] / total, 1)
        stated_midpoint = round((lo + min(hi, 1.0)) / 2 * 100, 1)
        buckets.append({
            "confidence_bucket": label,
            "stated_confidence_midpoint": stated_midpoint,
            "actual_accuracy_pct": actual_accuracy,
            "sample_size": total,
            "gap": round(actual_accuracy - stated_midpoint, 1),
        })

    return {
        "buckets": buckets,
        "total_judged": sum(b["sample_size"] for b in buckets),
    }


def compute_per_ticker_accuracy(price_lookup: dict, min_age_days: int = DEFAULT_MIN_AGE_DAYS) -> dict:
    """
    Per-ticker usage + accuracy, for showing directly in the Screener's
    results table. Deliberately takes an already-fetched price_lookup
    ({ticker: current_price}) rather than calling get_price_history
    itself — the Screener's daily snapshot already has current prices
    for every ticker it shows, so reusing them here avoids a second,
    redundant network call per row purely to check accuracy.

    Returns {ticker: {"times_analyzed": int, "accuracy_pct": float or
    None, "judged_count": int}}. A ticker with no judged calls yet gets
    accuracy_pct=None (not 0%) — there's nothing to be confident about
    either way yet, and 0% would misleadingly read as "always wrong."
    """
    raw_items = storage.get_all_analyses()
    now = datetime.now(_ET)
    cutoff = now - timedelta(days=min_age_days)

    per_ticker = {}
    for item in raw_items:
        ticker = item.get("ticker")
        if not ticker:
            continue
        entry = per_ticker.setdefault(ticker, {"times_analyzed": 0, "correct": 0, "incorrect": 0})
        entry["times_analyzed"] += 1

        if not item.get("baseline_price"):
            continue
        try:
            cached_at = datetime.fromisoformat(item["cached_at"])
        except Exception:
            continue
        if cached_at > cutoff:
            continue  # too recent to judge yet

        current_price = price_lookup.get(ticker)
        if current_price is None:
            continue  # not in the current snapshot — skip rather than fetch

        try:
            baseline_price = float(item["baseline_price"])
        except (TypeError, ValueError):
            continue

        pct_change = ((current_price - baseline_price) / baseline_price) * 100
        verdict = _classify(item.get("recommendation", "UNKNOWN"), pct_change)
        if verdict == "correct":
            entry["correct"] += 1
        elif verdict == "incorrect":
            entry["incorrect"] += 1

    result = {}
    for ticker, entry in per_ticker.items():
        judged = entry["correct"] + entry["incorrect"]
        result[ticker] = {
            "times_analyzed": entry["times_analyzed"],
            "judged_count": judged,
            "accuracy_pct": round(100 * entry["correct"] / judged, 1) if judged else None,
        }
    return result
