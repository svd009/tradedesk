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

# Persistent storage for the daily analysis cache — same AWS account and
# region already used for Bedrock, no separate credentials needed. Table
# is provisioned separately from the application runtime.
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "tradedesk_analyses")
# A production deployment must not appear healthy while silently storing
# analyses only in process memory. Local development can opt into the
# original graceful fallback with FAIL_ON_STORAGE_ERROR=false.
FAIL_ON_STORAGE_ERROR = os.getenv("FAIL_ON_STORAGE_ERROR", "true").strip().lower() == "true"

# ── Model provider ────────────────────────────────────────────────────────────
# "anthropic" → uses Anthropic API directly (paused — out of credits)
# "bedrock"   → uses AWS Bedrock (active — running on AWS free credit)
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "bedrock").lower()

# ── Model selection ───────────────────────────────────────────────────────────
# Haiku  → all 5 subagents (fast, cheap, focused single-domain tasks)
# Sonnet → synthesis agent (complex conflict resolution + extended thinking)
MODEL_FAST      = "claude-haiku-5-5"
MODEL_REASONING = "claude-sonnet-5-5"

# Bedrock model IDs (used when MODEL_PROVIDER = "bedrock")
# These are cross-region inference profile IDs, not raw model IDs — Bedrock
# rejects on-demand InvokeModel calls to the raw model ID for these newer
# Claude models and requires an inference profile instead.
# Using the US regional profile (not Global) — the Global profile
# rejected every request that included tools (ValidationException),
# confirmed and fixed early in this project; the US profile has been
# working correctly ever since via the Converse API path in
# src/client/model_client.py.
#
# To roll back to the previous generation, swap these two lines for:
#   BEDROCK_MODEL_FAST      = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
#   BEDROCK_MODEL_REASONING = "us.anthropic.claude-sonnet-4-6"
# model_client.py detects the generation from the ID and sends the right
# thinking settings for each, so nothing else needs to change.
# Both can be overridden with environment variables of the same name.
BEDROCK_MODEL_FAST = os.getenv(
    "BEDROCK_MODEL_FAST",
    "us.anthropic.claude-haiku-5-5",
)
BEDROCK_MODEL_REASONING = os.getenv(
    "BEDROCK_MODEL_REASONING",
    "us.anthropic.claude-sonnet-5-5",
)
# Two regions, because they can legitimately differ:
#   BEDROCK_REGION        general AWS region. storage.py uses it for the
#                         DynamoDB table. Set with AWS_REGION.
#   BEDROCK_MODEL_REGION  where the AI model calls are sent. A Bedrock API
#                         key belongs to the region it was created in, so
#                         this must match that region. Set with
#                         BEDROCK_MODEL_REGION; defaults to AWS_REGION.
BEDROCK_REGION          = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_REGION    = os.getenv("BEDROCK_MODEL_REGION") or BEDROCK_REGION

# ── Bedrock authentication ────────────────────────────────────────────────────
# Two ways to authenticate to Bedrock, and boto3 handles both on its own:
#   1. A Bedrock API key in AWS_BEARER_TOKEN_BEDROCK (what this project uses).
#   2. Classic IAM keys in AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
#   3. No keys at all: an AWS profile locally, or an attached role when
#      running on AWS (ECS / App Runner). This is the preferred setup.
# Important: a Bedrock API key ONLY works for Bedrock. DynamoDB (the daily
# cache in storage.py) needs IAM credentials (option 2 or 3).
BEDROCK_API_KEY_SET = bool(os.getenv("AWS_BEARER_TOKEN_BEDROCK"))
IAM_KEYS_SET = bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))

# ── Subagent settings ─────────────────────────────────────────────────────────
MAX_TOKENS_SUBAGENT   = 1500   # each subagent is focused — doesn't need much
MAX_TOKENS_SYNTHESIS  = 8000   # On the 5.x models, thinking tokens count
                                # toward this limit (it caps thinking + answer
                                # together), so it needs real headroom or the
                                # JSON answer gets cut off mid-way.
SYNTHESIS_EFFORT      = "medium"  # 5.x models: how hard the synthesis agent
                                # thinks. low | medium | high | xhigh | max.
                                # Higher = better reasoning, slower, costlier.
THINKING_BUDGET       = 3000   # Only used by 4.x models (rollback path).
                                # The 5.x models reject a fixed budget and
                                # use SYNTHESIS_EFFORT instead.

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

# Concurrency cap: how many analyses can run AT THE SAME TIME, across
# every user combined — a different concern from the rate limiter above
# (that one paces one person's requests over time; this one bounds total
# simultaneous load). Not required at current traffic, built primarily
# as a system-design learning piece — see src/orchestrator/concurrency_limiter.py.
CONCURRENCY_CAP = 10
SEC_FILING_CHARS      = 8000   # max chars to extract from SEC filings

# ── Cache freshness ───────────────────────────────────────────────────────────
# A saved analysis is reused only within the same market session (see
# analysis_cache.py). On top of that, if the price has moved by this many
# percent since the analysis was made, it is refreshed regardless.
PRICE_MOVE_REFRESH_PCT = 3.0
# Show a small "Updated 2:05 PM ET" line on each result.
SHOW_UPDATED_TIME = True

# ── Cost tracking ─────────────────────────────────────────────────────────────
# US dollars per million tokens (input, output), matched by a piece of the
# model ID. Used only to estimate cost in logs and stored usage records.
# Models not listed here are logged with token counts and no cost, so add
# a line here (from the Bedrock pricing page) when changing models.
MODEL_PRICES_PER_MTOK = {
    "claude-haiku-4-5":  (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}

# ── Portfolio defaults ────────────────────────────────────────────────────────
# Used as example portfolio in demo mode
DEMO_PORTFOLIO = {
    "NVDA": 0.35,
    "MSFT": 0.25,
    "AAPL": 0.20,
    "JPM":  0.20,
}

# ── Evaluation ────────────────────────────────────────────────────────────────
EVAL_PASS_THRESHOLD = 7.0   # out of 10

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
