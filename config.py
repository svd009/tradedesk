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
# Fallback for when Yahoo Finance is rate-limited/unreachable (this happens
# more often than you'd expect on shared cloud hosting — see market_data.py).
# Free tier, get a key at twelvedata.com. Only used as a fallback, so its
# low free-tier request limit (a few requests/minute) is not a bottleneck.
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

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
MAX_TOKENS_SYNTHESIS  = 3500   # reduced from 6000 — bull/bear case is now
                                # bullet points, not paragraphs, so it needs
                                # less room; generation time scales with
                                # this number, so this is a direct latency cut
THINKING_BUDGET       = 3000   # reduced from 4000 — a middle ground: still
                                # a real latency cut, but keeps enough room
                                # for genuine step-by-step conflict
                                # resolution across all 5 signals rather
                                # than risking shallower reasoning on the
                                # one step where missing something matters most

# ── Data settings ─────────────────────────────────────────────────────────────
PRICE_HISTORY_DAYS    = 600    # ~20 months — covers back to ~Jan 2025 for the
                                # chart, and comfortably supports SMA 200
                                # (which needs 200+ trading days; 180 days
                                # was never enough, so that overlay silently
                                # produced no line at all until this fix)
SUBAGENT_BATCH_TIME_BUDGET_SECONDS = 45  # max wait for the 5 parallel subagents
                                # combined — whichever haven't finished by
                                # then are marked unavailable rather than
                                # blocking every user on the single slowest one

# Token bucket rate limiter (per browser session — see rate_limiter.py for
# why session-scoped rather than IP-scoped). 5 requests, refilling 1 every
# 10 minutes, lets someone run a handful of tickers back-to-back (a normal
# burst of curiosity) while capping sustained hammering of the button.
RATE_LIMIT_BUCKET_CAPACITY = 5
RATE_LIMIT_REFILL_SECONDS_PER_TOKEN = 600
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
