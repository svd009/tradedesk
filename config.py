"""
config.py
──────────
Central configuration for TradeDesk.

The most important design decision in this file is the MODEL_PROVIDER
setting. Switching from "anthropic" to "bedrock" is literally one line
change here — the entire rest of the system is unaffected because every
agent goes through the abstracted ModelClient in src/client/model_client.py.

This is what "Bedrock-ready" means architecturally: the cloud migration
cost is one config line, not a rewrite.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API credentials ───────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# Powers the search_market_news tool (News and Macro agents). Bedrock has no
# equivalent of Anthropic's native web_search tool, so live search runs
# through this instead — works identically regardless of MODEL_PROVIDER.
# Free tier: 1,000 searches/month, no card required. Get a key at tavily.com.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# ── Model provider ────────────────────────────────────────────────────────────
# "anthropic" → uses Anthropic API directly (paused — out of credits)
# "bedrock"   → uses AWS Bedrock (active — running on AWS free credit)
MODEL_PROVIDER = "bedrock"

# ── Model selection ───────────────────────────────────────────────────────────
# Haiku  → all 5 subagents (fast, cheap, focused single-domain tasks)
# Sonnet → synthesis agent (complex conflict resolution + extended thinking)
MODEL_FAST      = "claude-haiku-4-5"
MODEL_REASONING = "claude-sonnet-4-6"

# Bedrock model IDs (used when MODEL_PROVIDER = "bedrock")
# These are cross-region inference profile IDs, not raw model IDs — Bedrock
# rejects on-demand InvokeModel calls to the raw model ID for these newer
# Claude models and requires an inference profile instead.
# Using the US regional profile (not Global) — the Global profile returned
# ValidationException on every request that included tools, while non-tool
# requests (e.g. synthesis) worked fine on Global. Testing whether tool use
# is supported on the US profile instead.
BEDROCK_MODEL_FAST      = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_MODEL_REASONING = "us.anthropic.claude-sonnet-4-6"
BEDROCK_REGION          = os.getenv("AWS_REGION", "us-east-2")

# ── Subagent settings ─────────────────────────────────────────────────────────
MAX_TOKENS_SUBAGENT   = 1500   # each subagent is focused — doesn't need much
MAX_TOKENS_SYNTHESIS  = 6000   # synthesis needs room for extended thinking
THINKING_BUDGET       = 4000   # tokens for extended thinking in synthesis

# ── Data settings ─────────────────────────────────────────────────────────────
PRICE_HISTORY_DAYS    = 180    # 6 months of price data for technical analysis
SEC_FILING_CHARS      = 8000   # max chars to extract from SEC filings
NEWS_SEARCH_RESULTS   = 5      # number of news results per search query

# ── Portfolio defaults ────────────────────────────────────────────────────────
# Used as example portfolio in demo mode
DEMO_PORTFOLIO = {
    "NVDA": 0.35,
    "MSFT": 0.25,
    "AAPL": 0.20,
    "JPM":  0.20,
}

# ── Recommendation thresholds ─────────────────────────────────────────────────
BUY_THRESHOLD  = 0.65   # composite score >= this → BUY
SELL_THRESHOLD = 0.35   # composite score <= this → SELL
                         # between thresholds → HOLD

# ── Evaluation ────────────────────────────────────────────────────────────────
EVAL_PASS_THRESHOLD = 7.0   # out of 10

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
