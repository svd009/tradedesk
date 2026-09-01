"""
scorecard.py
─────────────
A four-category, 0-10 scorecard (Performance, Valuation, Growth,
Profitability), inspired directly by Tickertape's own "Scorecard"
feature. Distills the 17-metric fundamentals grid into something
glanceable, the way Tickertape's four cards let someone size up a
stock before reading the full detail.

Deliberately NOT another AI call:
  Every score here is a plain, transparent, rule-based calculation from
  data TradeDesk already fetches (Yahoo Finance fundamentals, price
  history). No new API cost, no new latency, and — unlike the AI
  synthesis's own recommendation — these scores are fully reproducible:
  the same inputs always produce the same score, and the exact
  thresholds are documented right here, not hidden in a model's
  reasoning. This is intentionally a different kind of signal than the
  AI's "Composite Score" badge elsewhere in the app: that one reflects
  the synthesis agent's judgment across all 5 subagents; this one is a
  simple, deterministic read of the raw fundamentals and price data
  alone.

Why these four categories specifically:
  They mirror Tickertape's own Scorecard categories directly. Growth
  and Profitability come from the income statement, Valuation from
  price-to-fundamentals ratios, and Performance from actual price
  return — the same split real screening tools use because it
  separates "is this a good business" (growth, profitability) from
  "is it a good price" (valuation) from "has it actually been working"
  (performance), three genuinely different questions.
"""


def _clamp(score: float) -> float:
    return max(0.0, min(10.0, score))


def _score_valuation(fundamentals: dict) -> tuple:
    """
    Lower PEG (P/E adjusted for growth) = more attractively valued.
    PEG is used as the primary signal since it's the most complete
    single valuation metric — a "cheap" P/E on a shrinking company
    isn't actually cheap, PEG accounts for that. Falls back to raw P/E
    if PEG isn't available (e.g. negative/no earnings growth).

    PEG < 1.0   → 9-10  (attractively priced relative to growth)
    PEG 1.0-1.5 → 7-8   (fair)
    PEG 1.5-2.5 → 5-6   (getting expensive)
    PEG 2.5-4.0 → 3-4   (expensive)
    PEG > 4.0   → 0-2   (very expensive)
    """
    peg = fundamentals.get("peg_ratio")
    if isinstance(peg, (int, float)) and peg > 0:
        if peg < 1.0:
            return _clamp(10 - peg), "Attractively valued relative to growth"
        if peg < 1.5:
            return _clamp(8 - (peg - 1.0) * 2), "Fairly valued"
        if peg < 2.5:
            return _clamp(6 - (peg - 1.5) * 1.0), "Getting expensive"
        if peg < 4.0:
            return _clamp(4 - (peg - 2.5) * 0.67), "Expensive"
        return _clamp(max(0, 2 - (peg - 4.0) * 0.3)), "Very expensive"

    # Fallback: raw P/E, less informative without a growth adjustment
    pe = fundamentals.get("pe_ratio")
    if isinstance(pe, (int, float)) and pe > 0:
        if pe < 15:
            return 8.0, "Low P/E (PEG unavailable)"
        if pe < 25:
            return 6.0, "Moderate P/E (PEG unavailable)"
        if pe < 40:
            return 4.0, "High P/E (PEG unavailable)"
        return 2.0, "Very high P/E (PEG unavailable)"

    return None, "Valuation data unavailable"


def _score_growth(fundamentals: dict) -> tuple:
    """
    Blends revenue and earnings YoY growth, weighted toward earnings
    growth since that's what ultimately matters to a shareholder, but
    revenue growth still counts since earnings growth alone can be
    driven by one-time cost cuts rather than real business expansion.

    >30% blended growth → 9-10
    15-30%              → 7-8
    5-15%               → 5-6
    0-5%                → 3-4
    negative            → 0-2
    """
    revenue_growth = fundamentals.get("revenue_growth_yoy")
    earnings_growth = fundamentals.get("earnings_growth_yoy")

    values, weights = [], []
    if isinstance(revenue_growth, (int, float)):
        values.append(revenue_growth * 100)
        weights.append(1)
    if isinstance(earnings_growth, (int, float)):
        values.append(earnings_growth * 100)
        weights.append(2)  # weighted higher

    if not values:
        return None, "Growth data unavailable"

    blended = sum(v * w for v, w in zip(values, weights)) / sum(weights)

    if blended > 30:
        return _clamp(9 + min(1, (blended - 30) / 30)), "Strong growth"
    if blended > 15:
        return _clamp(7 + (blended - 15) / 15), "Solid growth"
    if blended > 5:
        return _clamp(5 + (blended - 5) / 10), "Moderate growth"
    if blended > 0:
        return _clamp(3 + blended / 5), "Slow growth"
    return _clamp(max(0, 2 + blended / 10)), "Declining"


def _score_profitability(fundamentals: dict) -> tuple:
    """
    Blends net profit margin (the bottom-line measure of profitability)
    with return on equity (how efficiently the company turns
    shareholder capital into profit) — margin alone doesn't capture
    capital efficiency, ROE alone doesn't capture whether the core
    business itself is actually profitable.

    >20% blended → 9-10  (excellent)
    10-20%       → 7-8   (good)
    5-10%        → 5-6   (moderate)
    0-5%         → 3-4   (thin)
    negative     → 0-2   (unprofitable)
    """
    margin = fundamentals.get("profit_margin")
    roe = fundamentals.get("return_on_equity")

    values = []
    if isinstance(margin, (int, float)):
        values.append(margin * 100)
    if isinstance(roe, (int, float)):
        values.append(min(roe * 100, 50))  # cap ROE's influence — very
                                            # high ROE is often leverage-
                                            # driven, not pure efficiency

    if not values:
        return None, "Profitability data unavailable"

    blended = sum(values) / len(values)

    if blended > 20:
        return _clamp(9 + min(1, (blended - 20) / 20)), "Excellent profitability"
    if blended > 10:
        return _clamp(7 + (blended - 10) / 10), "Good profitability"
    if blended > 5:
        return _clamp(5 + (blended - 5) / 5), "Moderate profitability"
    if blended > 0:
        return _clamp(3 + blended / 5), "Thin margins"
    return _clamp(max(0, 2 + blended / 5)), "Unprofitable"


def _score_performance(price_data: dict) -> tuple:
    """
    Based on real price return over the fetched history window — the
    most literal read of "has this stock actually been working,"
    separate from whether the business itself looks good on paper.
    Deliberately just price return, not a technical signal, that's
    what the Technical subagent already covers elsewhere.

    >40% return → 9-10
    15-40%      → 7-8
    0-15%       → 5-6
    -15-0%      → 3-4
    <-15%       → 0-2
    """
    pct_change = price_data.get("price_change_pct")
    if not isinstance(pct_change, (int, float)):
        return None, "Price history unavailable"

    if pct_change > 40:
        return _clamp(9 + min(1, (pct_change - 40) / 40)), "Strong price performance"
    if pct_change > 15:
        return _clamp(7 + (pct_change - 15) / 25), "Good price performance"
    if pct_change > 0:
        return _clamp(5 + pct_change / 15), "Modest gains"
    if pct_change > -15:
        return _clamp(3 + (pct_change + 15) / 15), "Modest decline"
    return _clamp(max(0, 2 + (pct_change + 15) / 15)), "Significant decline"


def compute_scorecard(fundamentals: dict, price_data: dict) -> dict:
    """
    Returns the four Tickertape-style scores, each as
    {"score": float 0-10 or None, "label": short description}.
    A None score means the underlying data wasn't available for that
    category — shown as "N/A" in the UI rather than a misleading 0.
    """
    valuation_score, valuation_label = _score_valuation(fundamentals)
    growth_score, growth_label = _score_growth(fundamentals)
    profitability_score, profitability_label = _score_profitability(fundamentals)
    performance_score, performance_label = _score_performance(price_data)

    return {
        "performance":   {"score": performance_score, "label": performance_label},
        "valuation":     {"score": valuation_score, "label": valuation_label},
        "growth":        {"score": growth_score, "label": growth_label},
        "profitability": {"score": profitability_score, "label": profitability_label},
    }
