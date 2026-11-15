"""
guardrails.py
──────────────
Automatic checks that sit around the AI, in three places:

  1. BEFORE the AI runs      check_market_data()
     Is the raw data sane? A price of zero, a 400% profit margin, or a
     "latest" price that is two weeks old means the source is wrong, and
     any analysis built on it would be wrong too.

  2. AFTER the final answer  validate_recommendation()
     Is the answer well-formed and self-consistent? Only allowed labels,
     confidence between 0 and 1, and no BUY sitting on a 3/10 score.

  3. AFTER the final answer  find_ungrounded_numbers()
     Does every number quoted in the summary actually appear in the data
     the AI was given? A figure that appears nowhere in the inputs was
     probably invented.

None of this uses AI. They are plain rules, so they are cheap, fast and
give the same answer every time. Problems are returned as short strings;
the orchestrator decides what to do with them (retry, flag, or stop).
"""

import json
import re
from datetime import date, datetime

VALID_RECOMMENDATIONS = ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")
_BULLISH = ("STRONG_BUY", "BUY")
_BEARISH = ("STRONG_SELL", "SELL")

# A weekend plus a holiday is 4 days; beyond a week the "latest" price is stale.
_MAX_PRICE_AGE_DAYS = 7
# Two independent sources disagreeing by more than this is a data problem.
PRICE_MISMATCH_PCT = 2.0


# ── 1. Input data checks ────────────────────────────────────────────────────

def check_market_data(price_data: dict, fundamentals: dict,
                      second_source_price: float = None) -> list:
    """Sanity-check raw price and fundamentals data. Returns a list of issues."""
    issues = []
    price_data = price_data or {}
    fundamentals = fundamentals or {}

    price = price_data.get("current_price")
    if price_data.get("error") or not price_data.get("closes"):
        issues.append("Price history is unavailable.")
    else:
        if not isinstance(price, (int, float)) or price != price or price <= 0:
            issues.append(f"Current price is not a valid positive number ({price!r}).")
        dates = price_data.get("dates") or []
        if dates:
            try:
                age = (date.today() - datetime.strptime(dates[-1][:10], "%Y-%m-%d").date()).days
                if age > _MAX_PRICE_AGE_DAYS:
                    issues.append(f"Latest price is {age} days old ({dates[-1][:10]}).")
            except ValueError:
                pass
        closes = price_data.get("closes") or []
        if len(closes) >= 2 and closes[-2]:
            jump = abs(closes[-1] - closes[-2]) / closes[-2] * 100
            if jump > 40:
                issues.append(f"Price moved {jump:.0f}% in one day; possible split or bad data.")
        if (isinstance(price, (int, float)) and second_source_price
                and second_source_price > 0):
            gap = abs(price - second_source_price) / second_source_price * 100
            if gap > PRICE_MISMATCH_PCT:
                issues.append(
                    f"Two price sources disagree by {gap:.1f}% "
                    f"({price} vs {second_source_price})."
                )

    if fundamentals.get("error"):
        issues.append("Fundamentals are unavailable.")
    else:
        # Ratios stored as fractions (0.25 = 25%). Values far outside any
        # real company's range mean a unit or data error.
        for field, low, high in (
            ("gross_margin", -1.0, 1.0),
            ("operating_margin", -5.0, 1.0),
            ("profit_margin", -5.0, 1.0),
            ("dividend_yield", 0.0, 0.5),
        ):
            value = fundamentals.get(field)
            if isinstance(value, (int, float)) and not (low <= value <= high):
                issues.append(f"{field} is outside a plausible range ({value}).")
        cap = fundamentals.get("market_cap_b")
        if isinstance(cap, (int, float)) and cap <= 0:
            issues.append("Market cap is zero or missing.")

    return issues


# ── 2. Output validation ────────────────────────────────────────────────────

def validate_recommendation(rec) -> tuple:
    """
    Check and lightly repair the final recommendation.

    Returns (rec, issues, fatal):
      rec     the recommendation, with safe repairs applied
      issues  list of human-readable problems found
      fatal   True if the answer is unusable and should not be shown
    """
    issues = []
    if not isinstance(rec, dict):
        return {}, ["The answer was not a JSON object."], True
    if rec.get("parse_error"):
        return rec, ["The answer could not be read as JSON."], True

    label = rec.get("recommendation")
    if isinstance(label, str):
        label = label.strip().upper().replace(" ", "_")
        rec["recommendation"] = label
    if label not in VALID_RECOMMENDATIONS:
        return rec, [f"Unknown recommendation label ({label!r})."], True

    conf = rec.get("confidence")
    if isinstance(conf, (int, float)) and 1 < conf <= 100:
        conf = conf / 100  # the model wrote 72 meaning 72%
        rec["confidence"] = round(conf, 4)
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        issues.append(f"Confidence was not between 0 and 1 ({conf!r}); set to 0.5.")
        rec["confidence"] = 0.5

    score = rec.get("composite_score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 10):
        issues.append(f"Composite score was not between 0 and 10 ({score!r}).")
        rec["composite_score"] = 5.0
    else:
        # The call and the score must tell the same story.
        if label in _BULLISH and score < 4.5:
            issues.append(f"{label} conflicts with a low composite score ({score}/10).")
        if label in _BEARISH and score > 6.5:
            issues.append(f"{label} conflicts with a high composite score ({score}/10).")

    if not str(rec.get("executive_summary") or "").strip():
        issues.append("Executive summary is missing.")
    for field in ("bull_case", "bear_case", "key_risks"):
        if not rec.get(field):
            issues.append(f"{field} is missing.")

    return rec, issues, False


# ── 3. Numeric grounding ────────────────────────────────────────────────────

_NUMBER = re.compile(r"(?<![\w.])-?\$?\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> list:
    out = []
    for raw in _NUMBER.findall(text or ""):
        cleaned = raw.replace("$", "").replace(",", "").rstrip(".")
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def _matches(claim: float, source: float) -> bool:
    """True if `claim` is `source`, allowing rounding and common rescaling."""
    claim, source = abs(claim), abs(source)
    for candidate in (source, source * 100, source / 100, source * 1000,
                      source / 1000, source / 1e6, source / 1e9):
        if candidate == 0:
            continue
        tolerance = max(candidate * 0.015, 0.06)  # 1.5%, or rounding to 1 decimal
        if abs(claim - candidate) <= tolerance:
            return True
    return False


def find_ungrounded_numbers(rec: dict, *sources) -> list:
    """
    Numbers quoted in the summary and bull/bear cases that do not appear
    anywhere in the data the AI was given.

    A heuristic, not proof: a number can be legitimately derived (a
    difference, a ratio) and still be flagged. So this only raises a
    flag for review; it never blocks a result on its own.
    """
    claim_text = " ".join(
        str(part) for field in ("executive_summary", "bull_case", "bear_case")
        for part in (rec.get(field) if isinstance(rec.get(field), list)
                     else [rec.get(field) or ""])
    )
    source_numbers = _numbers_in(json.dumps(sources, default=str))

    ungrounded = []
    for claim in _numbers_in(claim_text):
        value = abs(claim)
        if value < 20 and float(value).is_integer():
            continue  # counts, "3-12 months", "top 5" and similar
        if 1900 <= value <= 2100 and float(value).is_integer():
            continue  # years
        if value in (20, 50, 100, 200):
            continue  # SMA periods and round thresholds
        if not any(_matches(claim, s) for s in source_numbers):
            ungrounded.append(claim)
    return sorted(set(ungrounded))
