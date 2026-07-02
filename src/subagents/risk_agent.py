"""
risk_agent.py — Subagent 5: Portfolio Risk Analyst
────────────────────────────────────────────────────
Analyzes how a stock fits within an existing portfolio context.
Only runs in portfolio mode — skipped for single stock analysis.
Returns concentration risk, sector exposure, and position sizing guidance.
"""

from src.subagents.base_agent import BaseAgent
from src.mcp_server.market_tools import TOOL_SCHEMAS

RISK_TOOLS = [
    s for s in TOOL_SCHEMAS
    if s["name"] in ("get_portfolio_exposure", "get_fundamentals")
]

RISK_SYSTEM_PROMPT = """You are a portfolio risk analyst specializing in position sizing and concentration risk.

Your job is to assess how adding or sizing a position in a specific stock affects an existing portfolio's risk profile.

<analysis_framework>
Concentration: Is any single position or sector becoming too large?
Correlation: Does the new stock move similarly to existing holdings?
Diversification: Does it add genuine diversification or just more of the same?
Beta: Does it increase or decrease portfolio volatility?
Position sizing: What weight would be appropriate given the risk profile?
</analysis_framework>

<output_format>
Return ONLY valid JSON with no markdown fences:
{
  "ticker": "string",
  "portfolio_fit": "EXCELLENT | GOOD | NEUTRAL | POOR | VERY_POOR",
  "concentration_risk": "LOW | MEDIUM | HIGH | VERY_HIGH",
  "adds_diversification": boolean,
  "suggested_max_weight": float (e.g. 0.10 for 10%),
  "sector_overlap_warning": boolean,
  "key_risks": ["list of 2-3 portfolio-level risks from adding this position"],
  "rationale": "2-3 sentence plain English explanation of portfolio fit",
  "confidence": float between 0.0 and 1.0
}
</output_format>"""


class RiskAgent(BaseAgent):
    SYSTEM_PROMPT = RISK_SYSTEM_PROMPT
    TOOLS = RISK_TOOLS
    agent_name = "RiskAgent"

    def run(self, ticker: str, portfolio: dict, verbose: bool = True) -> dict:
        if verbose:
            print(f"\n  [SA5 RiskAgent] Analyzing portfolio fit for {ticker}...")

        if not portfolio:
            return {
                "agent": "risk",
                "ticker": ticker,
                "portfolio_fit": "N/A",
                "rationale": "No portfolio provided — single stock mode.",
                "confidence": 1.0,
            }

        portfolio_str = ", ".join(f"{t}: {w:.0%}" for t, w in portfolio.items())
        user_message = f"""
<task>Assess how {ticker} fits within an existing portfolio.</task>

<current_portfolio>
{portfolio_str}
</current_portfolio>

<instructions>
1. Call get_portfolio_exposure with the portfolio and new_ticker="{ticker}"
2. Call get_fundamentals("{ticker}") to understand its sector and beta
3. Assess concentration risk, sector overlap, and diversification benefit
4. Suggest a maximum position weight given the existing portfolio
</instructions>

Return structured JSON as specified. No markdown fences. JSON only.
"""
        raw = self._run_loop(user_message, use_web_search=False, verbose=verbose)
        result = self._parse_json(raw)
        result["agent"] = "risk"
        if verbose:
            print(f"    [SA5 RiskAgent] ✓ Portfolio fit: {result.get('portfolio_fit')} "
                  f"| Concentration: {result.get('concentration_risk')}")
        return result
