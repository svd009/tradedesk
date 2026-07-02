"""
news_agent.py — Subagent 1: News & Sentiment Analyst
──────────────────────────────────────────────────────
Scans recent news, earnings releases, and analyst commentary using
Claude's native web search tool. Returns a structured sentiment
assessment with key events ranked by materiality.

Why web search rather than a news API?
  Web search gives real-time access to any source — financial news,
  SEC press releases, analyst blogs, earnings call transcripts —
  without requiring a paid API key. Claude's built-in web search tool
  handles this natively, making it the right choice for a free-tier
  demo that still produces genuinely useful results.
"""

from src.subagents.base_agent import BaseAgent
from src.mcp_server.market_tools import MarketToolExecutor, TOOL_SCHEMAS


NEWS_SYSTEM_PROMPT = """You are a financial news and sentiment analyst specializing in equity research.

Your job is to assess the current news environment for a stock and return a structured sentiment analysis.

<research_process>
1. Search for recent news about the company (last 30-60 days) covering:
   - Earnings results and guidance
   - Product launches or strategic announcements
   - Analyst upgrades/downgrades
   - Regulatory developments
   - Management changes
   - Competitive threats or wins
2. Search for broader sector/macro news affecting this company
3. Assess overall sentiment and identify the 3-5 most material events
</research_process>

<output_format>
Return ONLY valid JSON with no markdown fences:
{
  "ticker": "string",
  "sentiment_score": float between -1.0 (very bearish) and 1.0 (very bullish),
  "sentiment_label": "VERY_BULLISH | BULLISH | NEUTRAL | BEARISH | VERY_BEARISH",
  "key_events": [
    {
      "event": "brief description",
      "impact": "POSITIVE | NEGATIVE | NEUTRAL",
      "materiality": "HIGH | MEDIUM | LOW"
    }
  ],
  "recent_catalysts": ["list of upcoming catalysts or events to watch"],
  "summary": "2-3 sentence plain English summary of the news environment",
  "confidence": float between 0.0 and 1.0
}
</output_format>"""


class NewsAgent(BaseAgent):
    SYSTEM_PROMPT = NEWS_SYSTEM_PROMPT
    TOOLS = []  # uses only web_search (native tool)
    agent_name = "NewsAgent"

    def run(self, ticker: str, company_name: str = "", verbose: bool = True) -> dict:
        if verbose:
            print(f"\n  [SA1 NewsAgent] Scanning news for {ticker}...")

        name = company_name or ticker
        user_message = f"""
<task>Analyze the current news and sentiment environment for {name} ({ticker}).</task>

<search_instructions>
Search for:
1. "{ticker} earnings results 2025" or "{name} quarterly results"
2. "{ticker} news analyst upgrade downgrade 2025"
3. "{name} product announcement strategic news"
4. "{ticker} stock outlook risk factors 2025"

Use 2-3 targeted searches to gather comprehensive coverage.
</search_instructions>

Return structured JSON as specified in your system prompt.
Do not include markdown fences. Return only the JSON object.
"""
        raw = self._run_loop(user_message, use_web_search=True, verbose=verbose)
        result = self._parse_json(raw)
        result["agent"] = "news_sentiment"
        if verbose:
            print(f"    [SA1 NewsAgent] ✓ Sentiment: {result.get('sentiment_label')} "
                  f"(score: {result.get('sentiment_score')})")
        return result
