"""
peer_comparison.py
────────────────────
A named peer-comparison table: how does this stock's P/E, margin,
growth, and ROE compare against its actual, real competitors, not an
aggregate sector average. Inspired by Tickertape's peer comparison
feature.

Why a curated peer map, not automatic sector matching:
  "Find this company's competitors automatically" sounds appealing but
  is genuinely hard to get right for arbitrary tickers (sector labels
  are broad and noisy — Amazon and Alibaba share a sector code with
  companies that aren't real competitors of either). A small, curated,
  factually-checked list of real competitor relationships for the most
  commonly analyzed names is more honest than an automatic match that
  might silently be wrong. Tickers outside this list simply don't get
  a peer comparison shown, rather than a guessed one.

Cost note:
  Building this table means fetching fundamentals for 2-4 extra
  tickers per analysis, real, bounded, predictable API calls, not the
  hundreds-at-once risk a full market screener would carry. Every one
  of those calls reuses the same cached, retrying, Twelve-Data-backed
  get_fundamentals() the rest of the app already relies on.
"""

# Peers are real, named competitors, not sector-mates — kept deliberately
# small and factually conservative rather than exhaustive.
PEER_MAP = {
    # US Technology
    "AAPL": ["MSFT", "GOOGL"],
    "MSFT": ["AAPL", "GOOGL", "AMZN"],
    "GOOGL": ["MSFT", "META"],
    "META": ["GOOGL", "SNAP"],
    "AMZN": ["MSFT", "WMT"],
    "NFLX": ["DIS"],
    "ADBE": ["CRM"],
    "CRM": ["ADBE", "NOW"],
    "ORCL": ["MSFT", "IBM"],
    "IBM": ["ORCL", "MSFT"],

    # US Semiconductors
    "NVDA": ["AMD", "INTC", "AVGO"],
    "AMD": ["NVDA", "INTC", "QCOM"],
    "INTC": ["NVDA", "AMD"],
    "QCOM": ["AMD", "AVGO"],
    "AVGO": ["QCOM", "NVDA"],
    "MU": ["INTC"],

    # US Finance
    "JPM": ["BAC", "WFC", "GS"],
    "BAC": ["JPM", "WFC", "C"],
    "WFC": ["JPM", "BAC"],
    "GS": ["MS", "JPM"],
    "MS": ["GS", "JPM"],
    "C": ["BAC", "WFC"],
    "V": ["MA", "AXP"],
    "MA": ["V", "AXP"],
    "AXP": ["V", "MA"],

    # US Consumer
    "KO": ["PEP"],
    "PEP": ["KO"],
    "MCD": ["SBUX", "CMG"],
    "SBUX": ["MCD", "CMG"],
    "WMT": ["COST", "TGT"],
    "COST": ["WMT", "TGT"],
    "TGT": ["WMT", "COST"],
    "NKE": ["LULU"],

    # US Auto
    "TSLA": ["F", "GM"],
    "F": ["GM", "TSLA"],
    "GM": ["F", "TSLA"],

    # US Healthcare
    "JNJ": ["PFE", "ABBV", "MRK"],
    "PFE": ["JNJ", "MRK", "ABBV"],
    "MRK": ["PFE", "JNJ"],
    "ABBV": ["JNJ", "PFE"],
    "UNH": ["CVS"],

    # US Energy
    "XOM": ["CVX"],
    "CVX": ["XOM"],

    # India — IT Services
    "TCS.NS": ["INFY.NS", "WIPRO.NS", "HCLTECH.NS"],
    "INFY.NS": ["TCS.NS", "WIPRO.NS", "HCLTECH.NS"],
    "WIPRO.NS": ["TCS.NS", "INFY.NS", "HCLTECH.NS"],
    "HCLTECH.NS": ["TCS.NS", "INFY.NS", "WIPRO.NS"],
    "TECHM.NS": ["TCS.NS", "INFY.NS"],

    # India — Banking
    "HDFCBANK.NS": ["ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
    "ICICIBANK.NS": ["HDFCBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
    "KOTAKBANK.NS": ["HDFCBANK.NS", "ICICIBANK.NS"],
    "AXISBANK.NS": ["HDFCBANK.NS", "ICICIBANK.NS"],
    "SBIN.NS": ["HDFCBANK.NS", "ICICIBANK.NS"],

    # India — FMCG
    "HINDUNILVR.NS": ["ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS"],
    "ITC.NS": ["HINDUNILVR.NS", "NESTLEIND.NS"],
    "NESTLEIND.NS": ["HINDUNILVR.NS", "ITC.NS", "BRITANNIA.NS"],
    "BRITANNIA.NS": ["HINDUNILVR.NS", "NESTLEIND.NS"],

    # India — Auto
    "TATAMOTORS.NS": ["MARUTI.NS", "M&M.NS"],
    "MARUTI.NS": ["TATAMOTORS.NS", "M&M.NS"],
    "M&M.NS": ["TATAMOTORS.NS", "MARUTI.NS"],

    # India — Energy
    "RELIANCE.NS": ["ONGC.NS", "BPCL.NS"],
    "ONGC.NS": ["RELIANCE.NS", "BPCL.NS"],
    "BPCL.NS": ["RELIANCE.NS", "ONGC.NS"],
}

# Metrics shown in the comparison table, in display order.
_COMPARISON_METRICS = [
    ("pe_ratio", "P/E Ratio", False),
    ("peg_ratio", "PEG Ratio", False),
    ("profit_margin", "Profit Margin", True),
    ("revenue_growth_yoy", "Revenue Growth", True),
    ("return_on_equity", "ROE", True),
]


def get_peers(ticker: str) -> list:
    return PEER_MAP.get(ticker.upper(), [])


def build_peer_comparison(ticker: str, fundamentals: dict, get_fundamentals_fn) -> dict:
    """
    Builds a comparison table for `ticker` against its known peers.
    Returns None if no peer mapping exists for this ticker — a
    deliberately curated, not exhaustive, list (see module docstring).

    Args:
        ticker:              the analyzed ticker
        fundamentals:        already-fetched fundamentals for `ticker`
                              (avoids a redundant fetch)
        get_fundamentals_fn: injected rather than imported directly, so
                              this module stays trivially testable
                              without needing real network access
    """
    peers = get_peers(ticker)
    if not peers:
        return None

    rows = [{"ticker": ticker.upper(), "is_subject": True, **fundamentals}]
    for peer in peers:
        peer_fund = get_fundamentals_fn(peer)
        if not peer_fund.get("error"):
            rows.append({"ticker": peer, "is_subject": False, **peer_fund})

    if len(rows) < 2:
        return None  # every peer fetch failed — nothing worth showing

    return {"metrics": _COMPARISON_METRICS, "rows": rows}
