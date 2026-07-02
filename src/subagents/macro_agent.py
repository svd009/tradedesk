"""
macro_agent.py — Subagent 4: Macro & Sector Analyst
──────────────────────────────────────────────────────
Evaluates the broader macro environment and sector positioning.
Returns headwinds/tailwinds and peer relative performance.
"""

from src.subagents.base_agent import BaseAgent
from src.mcp_server.market_tools import TOOL_SCHEMAS

MACRO_TOOLS = [
    s for s in TOOL_SCHEMAS
    if s["name"] == "get_sector_comparison"
]

MACRO_SYSTEM_PROMPT = """You are a macro and sector analyst specializing in top-down equity research.

Your job is to assess how the current macroeconomic environment and sector dynamics affect a specific stock.

<analysis_framework>
Sector momentum: Is the stock's sector outperforming or underperforming the market?
Macro tailwinds: What macro trends favor this company? (AI spending, interest rates, consumer trends)
Macro headwinds: What macro risks threaten this company? (rate environment, regulation, FX)
Relative positioning: How does this stock compare to sector peers?
Rotation signals: Is money flowing into or out of this sector?
</analysis_framework>

<output_format>
Return ONLY valid JSON with no markdown fences:
{
  "ticker": "string",
  "macro_stance": "FAVORABLE | NEUTRAL | UNFAVORABLE",
  "sector_momentum": "STRONG | MODERATE | WEAK | NEGATIVE",
  "tailwinds": ["list of 2-3 macro or sector tailwinds"],
  "headwinds": ["list of 2-3 macro or sector headwinds"],
  "sector_relative_performance": "OUTPERFORMING | IN_LINE | UNDERPERFORMING",
  "market_relative_performance": "OUTPERFORMING | IN_LINE | UNDERPERFORMING",
  "key_macro_risks": ["list of top 2 macro risks to monitor"],
  "summary": "2-3 sentence plain English summary of macro and sector context",
  "confidence": float between 0.0 and 1.0
}
</output_format>"""


class MacroAgent(BaseAgent):
    SYSTEM_PROMPT = MACRO_SYSTEM_PROMPT
    TOOLS = MACRO_TOOLS
    agent_name = "MacroAgent"

    def run(self, ticker: str, company_name: str = "", verbose: bool = True) -> dict:
        if verbose:
            print(f"\n  [SA4 MacroAgent] Analyzing macro context for {ticker}...")

        name = company_name or ticker
        user_message = f"""
<task>Assess the macro and sector environment for {name} ({ticker}).</task>

<instructions>
1. Call get_sector_comparison("{ticker}") to get relative performance data
2. Use web search to assess current macro environment relevant to this company:
   - Search: "current macro environment {ticker} sector outlook 2025"
   - Search: "{name} competitive landscape sector trends 2025"
3. Identify specific tailwinds and headwinds for this company given macro conditions
</instructions>

Return structured JSON as specified. No markdown fences. JSON only.
"""
        raw = self._run_loop(user_message, use_web_search=True, verbose=verbose)
        result = self._parse_json(raw)
        result["agent"] = "macro_sector"
        if verbose:
            print(f"    [SA4 MacroAgent] ✓ Macro stance: {result.get('macro_stance')} "
                  f"| Sector: {result.get('sector_momentum')}")
        return result
