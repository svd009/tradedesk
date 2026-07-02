"""
technical_agent.py — Subagent 3: Technical Analyst
────────────────────────────────────────────────────
Analyzes price action, momentum indicators, and chart patterns.
Returns a structured technical signal with trend direction and key levels.
"""

from src.subagents.base_agent import BaseAgent
from src.mcp_server.market_tools import TOOL_SCHEMAS

TECHNICAL_TOOLS = [
    s for s in TOOL_SCHEMAS
    if s["name"] == "get_price_and_technicals"
]

TECHNICAL_SYSTEM_PROMPT = """You are a technical analyst specializing in equity price action and momentum analysis.

Your job is to assess a stock's technical picture using price data, moving averages, momentum indicators, and volume signals.

<analysis_framework>
Trend: Is the stock in an uptrend, downtrend, or sideways range? (price vs SMA 20/50/200)
Momentum: Is momentum accelerating or fading? (RSI, MACD)
Structure: Golden cross / death cross? Near support or resistance?
Volume: Is recent price action confirmed by volume?
Risk/Reward: How far is the stock from key levels?
</analysis_framework>

<interpretation_guide>
RSI > 70: Overbought — potential pullback risk
RSI < 30: Oversold — potential bounce opportunity
RSI 40-60: Neutral momentum
MACD above signal: Bullish momentum
Golden cross (SMA50 > SMA200): Long-term bullish structure
Death cross (SMA50 < SMA200): Long-term bearish structure
</interpretation_guide>

<output_format>
Return ONLY valid JSON with no markdown fences:
{
  "ticker": "string",
  "technical_signal": "STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL",
  "trend_direction": "UPTREND | SIDEWAYS | DOWNTREND",
  "momentum": "STRONG | MODERATE | WEAK | NEGATIVE",
  "rsi_assessment": "OVERBOUGHT | NEUTRAL | OVERSOLD",
  "macd_assessment": "BULLISH | NEUTRAL | BEARISH",
  "key_levels": {
    "support": float,
    "resistance": float,
    "current_price": float
  },
  "risk_reward": "FAVORABLE | NEUTRAL | UNFAVORABLE",
  "pattern_notes": "Brief description of notable chart pattern or structure",
  "summary": "2-3 sentence plain English summary of technical picture",
  "confidence": float between 0.0 and 1.0
}
</output_format>"""


class TechnicalAgent(BaseAgent):
    SYSTEM_PROMPT = TECHNICAL_SYSTEM_PROMPT
    TOOLS = TECHNICAL_TOOLS
    agent_name = "TechnicalAgent"

    def run(self, ticker: str, verbose: bool = True) -> dict:
        if verbose:
            print(f"\n  [SA3 TechnicalAgent] Analyzing technicals for {ticker}...")

        user_message = f"""
<task>Perform a technical analysis of {ticker}.</task>

<instructions>
1. Call get_price_and_technicals("{ticker}") to retrieve all price and indicator data
2. Interpret the RSI, MACD, moving averages, cross signals, and volume
3. Identify key support and resistance levels
4. Assess the overall technical picture and risk/reward setup
</instructions>

Return structured JSON as specified. No markdown fences. JSON only.
"""
        raw = self._run_loop(user_message, use_web_search=False, verbose=verbose)
        result = self._parse_json(raw)
        result["agent"] = "technical"
        if verbose:
            print(f"    [SA3 TechnicalAgent] ✓ Signal: {result.get('technical_signal')} "
                  f"| Trend: {result.get('trend_direction')}")
        return result
