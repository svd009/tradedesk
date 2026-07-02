"""
market_tools.py
────────────────
MCP tool server exposing market data, technical analysis, SEC filings,
and portfolio utilities as standardized tools to all TradeDesk subagents.

Why MCP here specifically?
  Each subagent is isolated — it has its own clean context and doesn't
  share state with other subagents. MCP provides the standardized
  interface through which every subagent accesses external data,
  regardless of which data source it needs. This means:
    - The News Agent calls search_news via MCP
    - The Technical Agent calls get_technical_analysis via MCP
    - The Fundamentals Agent calls get_fundamentals via MCP
  All three go through the same protocol, making the tool layer
  independently testable and swappable — exactly the "auditable
  workflow" pattern from the MCP Advanced Topics course.

Six tools exposed:
  1. get_price_and_technicals  — price history + all TA indicators
  2. get_fundamentals          — valuation ratios + financial health
  3. get_sector_comparison     — sector ETF vs market performance
  4. get_sec_filings           — recent 10-K/10-Q/8-K metadata
  5. search_market_news        — Claude web search for recent news
  6. get_portfolio_exposure    — concentration + correlation analysis
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.data.market_data import get_price_history, get_fundamentals, get_sector_performance
from src.data.sec_filings import get_filing_summary
from src.data.technical_indicators import run_full_technical_analysis


# ── Tool schemas ──────────────────────────────────────────────────────────────
# These are sent to each subagent so it knows which tools exist and
# exactly how to call them. Clear descriptions are critical — the agent
# uses them to decide WHEN to call each tool.

TOOL_SCHEMAS = [
    {
        "name": "get_price_and_technicals",
        "description": (
            "Fetch 6-month price history and compute all technical analysis "
            "indicators for a stock ticker: moving averages (SMA 20/50/200), "
            "RSI, MACD, golden/death cross, support/resistance levels, and "
            "volume signals. Use this to assess price trend and momentum."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol e.g. 'NVDA', 'AAPL'"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_fundamentals",
        "description": (
            "Fetch fundamental financial metrics for a stock: P/E ratio, "
            "revenue growth, profit margins, debt-to-equity, free cash flow, "
            "return on equity, analyst recommendations, and recent earnings "
            "surprises. Use this to assess financial health and valuation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_sector_comparison",
        "description": (
            "Compare a stock's 90-day performance against its sector ETF and "
            "the S&P 500. Returns whether the stock is outperforming or "
            "underperforming its sector and the broader market."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_sec_filings",
        "description": (
            "Fetch recent SEC filing metadata for a company: most recent "
            "10-K annual report, 10-Q quarterly report, and count of recent "
            "8-K material event filings. Use this to assess reporting "
            "regularity and check for recent material events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "search_market_news",
        "description": (
            "Search for recent news, analyst reports, earnings releases, and "
            "market commentary about a stock or topic. Returns structured "
            "search results with titles, snippets, and publication context. "
            "Use this to assess sentiment and identify recent material events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query e.g. 'NVDA earnings Q1 2025' or "
                        "'Nvidia AI chip demand outlook 2025'"
                    )
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_portfolio_exposure",
        "description": (
            "Analyze a portfolio's current exposure: sector concentration, "
            "position sizing, and how a new ticker would affect the overall "
            "portfolio risk profile. Use this in portfolio mode to assess "
            "fit and concentration risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio": {
                    "type": "object",
                    "description": "Dict of {ticker: weight} e.g. {'NVDA': 0.3, 'AAPL': 0.2}",
                },
                "new_ticker": {
                    "type": "string",
                    "description": "Ticker being considered for addition (optional)",
                }
            },
            "required": ["portfolio"]
        }
    },
]

# Web search tool schema — passed to Claude alongside our custom tools
# so the News and Macro agents can search the web directly
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}


class MarketToolExecutor:
    """
    Executes market data tool calls and returns JSON string results.
    Used directly by subagents — not via MCP protocol overhead.
    """

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call by name and return a JSON string result."""
        handlers = {
            "get_price_and_technicals": self._price_and_technicals,
            "get_fundamentals":         self._fundamentals,
            "get_sector_comparison":    self._sector_comparison,
            "get_sec_filings":          self._sec_filings,
            "get_portfolio_exposure":   self._portfolio_exposure,
            # search_market_news is handled by Claude's native web_search tool
            # so it doesn't need an executor here
        }
        handler = handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            return json.dumps(handler(**tool_input), indent=2, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _price_and_technicals(self, ticker: str) -> dict:
        price_data = get_price_history(ticker)
        if price_data.get("error"):
            return price_data
        tech = run_full_technical_analysis(price_data)
        # Strip raw arrays to keep context lean — agents need signals, not 180 floats
        tech.pop("closes", None)
        tech.pop("volumes", None)
        return tech

    def _fundamentals(self, ticker: str) -> dict:
        return get_fundamentals(ticker)

    def _sector_comparison(self, ticker: str) -> dict:
        return get_sector_performance(ticker)

    def _sec_filings(self, ticker: str) -> dict:
        return get_filing_summary(ticker)

    def _portfolio_exposure(self, portfolio: dict,
                            new_ticker: str = None) -> dict:
        """
        Analyze portfolio exposure across sectors and positions.
        Computes concentration risk and basic correlation proxy.
        """
        if not portfolio:
            return {"error": "Empty portfolio provided"}

        # Fetch sector for each holding
        holdings_detail = []
        sector_exposure = {}

        for ticker, weight in portfolio.items():
            fund = get_fundamentals(ticker)
            sector = fund.get("sector", "Unknown")
            sector_exposure[sector] = sector_exposure.get(sector, 0) + weight
            holdings_detail.append({
                "ticker": ticker,
                "weight": weight,
                "sector": sector,
                "market_cap_b": fund.get("market_cap_b"),
                "beta": fund.get("beta"),
            })

        # Concentration risk: largest single position
        max_position = max(portfolio.values()) if portfolio else 0
        concentration_risk = (
            "HIGH" if max_position > 0.35
            else "MEDIUM" if max_position > 0.20
            else "LOW"
        )

        # Sector concentration
        max_sector_weight = max(sector_exposure.values()) if sector_exposure else 0
        sector_concentration = (
            "HIGH" if max_sector_weight > 0.50
            else "MEDIUM" if max_sector_weight > 0.35
            else "LOW"
        )

        result = {
            "portfolio_size": len(portfolio),
            "total_weight": round(sum(portfolio.values()), 4),
            "holdings": holdings_detail,
            "sector_exposure": sector_exposure,
            "largest_position": max(portfolio, key=portfolio.get) if portfolio else None,
            "largest_position_weight": max_position,
            "concentration_risk": concentration_risk,
            "sector_concentration": sector_concentration,
        }

        # If a new ticker is provided, assess how it affects the portfolio
        if new_ticker:
            new_fund = get_fundamentals(new_ticker)
            new_sector = new_fund.get("sector", "Unknown")
            existing_sector_weight = sector_exposure.get(new_sector, 0)
            result["new_ticker_analysis"] = {
                "ticker": new_ticker,
                "sector": new_sector,
                "adds_sector_concentration": existing_sector_weight > 0.20,
                "existing_exposure_in_sector": existing_sector_weight,
                "recommendation_note": (
                    f"Adding {new_ticker} would increase {new_sector} "
                    f"exposure from {existing_sector_weight:.0%} — "
                    f"{'HIGH CONCENTRATION RISK' if existing_sector_weight > 0.35 else 'within acceptable range'}"
                )
            }

        return result
