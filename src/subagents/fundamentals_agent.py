"""
fundamentals_agent.py — Subagent 2: Fundamental Analyst
──────────────────────────────────────────────────────────
Analyzes financial health, valuation metrics, and SEC filing regularity.
Returns a structured fundamental health assessment.
"""

from src.subagents.base_agent import BaseAgent
from src.mcp_server.market_tools import MarketToolExecutor, TOOL_SCHEMAS

# Only expose the tools this agent actually needs
FUNDAMENTALS_TOOLS = [
    s for s in TOOL_SCHEMAS
    if s["name"] in ("get_fundamentals", "get_sec_filings")
]

FUNDAMENTALS_SYSTEM_PROMPT = """You are a fundamental equity analyst specializing in financial statement analysis.

Your job is to assess a company's financial health, valuation, and reporting quality.

<analysis_framework>
Valuation: Is the stock cheap or expensive relative to growth? (PEG ratio, P/E vs sector)
Profitability: Are margins expanding or contracting? Is ROE healthy?
Balance sheet: Can the company sustain itself? (debt-to-equity, current ratio)
Growth: Is revenue and earnings growth accelerating or decelerating?
Earnings quality: Has the company been beating or missing estimates consistently?
Reporting: Is the company filing on schedule with no regulatory concerns?
</analysis_framework>

<output_format>
Return ONLY valid JSON with no markdown fences:
{
  "ticker": "string",
  "company_name": "string",
  "fundamental_score": float between 1.0 (very weak) and 10.0 (very strong),
  "valuation": "CHEAP | FAIR | EXPENSIVE | VERY_EXPENSIVE",
  "financial_health": "STRONG | ADEQUATE | WEAK | VERY_WEAK",
  "growth_trajectory": "ACCELERATING | STABLE | DECELERATING | NEGATIVE",
  "key_strengths": ["list of 2-3 fundamental strengths"],
  "key_risks": ["list of 2-3 fundamental risks or red flags"],
  "earnings_trend": "BEATING | IN_LINE | MISSING",
  "sec_filing_status": "ON_SCHEDULE | REVIEW_NEEDED | NOT_AVAILABLE",
  "summary": "2-3 sentence plain English summary of fundamental health",
  "confidence": float between 0.0 and 1.0
}
</output_format>"""


class FundamentalsAgent(BaseAgent):
    SYSTEM_PROMPT = FUNDAMENTALS_SYSTEM_PROMPT
    TOOLS = FUNDAMENTALS_TOOLS
    agent_name = "FundamentalsAgent"

    def run(self, ticker: str, verbose: bool = True) -> dict:
        if verbose:
            print(f"\n  [SA2 FundamentalsAgent] Analyzing fundamentals for {ticker}...")

        user_message = f"""
<task>Perform a fundamental analysis of {ticker}.</task>

<instructions>
1. Call get_fundamentals("{ticker}") to retrieve all financial metrics
2. Call get_sec_filings("{ticker}") to assess reporting regularity
3. Synthesize both into a structured fundamental assessment

Pay particular attention to:
- Revenue and earnings growth rates
- Profit margin trends
- Debt levels relative to cash flow
- Whether the stock appears fairly valued given its growth rate (PEG ratio)
- Any earnings surprises (positive or negative)
</instructions>

Return structured JSON as specified. No markdown fences. JSON only.
"""
        raw = self._run_loop(user_message, use_web_search=False, verbose=verbose)
        result = self._parse_json(raw)
        result["agent"] = "fundamentals"
        if verbose:
            print(f"    [SA2 FundamentalsAgent] ✓ Score: {result.get('fundamental_score')}/10 "
                  f"| Valuation: {result.get('valuation')}")
        return result
