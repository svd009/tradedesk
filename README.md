# TradeDesk: Multi-Subagent Equity Research & Portfolio Intelligence

> Built with Claude (via AWS Bedrock): 5 parallel subagents, extended-thinking synthesis, real live data, persistent daily caching, a public track record, and a token bucket rate limiter.

**Status: live and publicly deployed.**

This is a research and educational tool, not financial advice. Nothing here should be the sole basis for a trade — consult a qualified financial adviser before making any investment decision.

---

## Live Demo

**[Launch TradeDesk →](https://tradedesk-svd009.streamlit.app/)**

Currently supports **US stocks** (e.g. `AAPL`, `NVDA`, no suffix needed) and **Indian stocks on NSE/BSE** (e.g. `RELIANCE.NS`, `TCS.BO`). Other exchanges aren't supported yet. Ticker input isn't case-sensitive, and there's a company-name search (554 companies, sourced from the S&P 500 and Nifty 50 index constituent lists) for anyone who doesn't know the exact ticker.

---

## What It Does

Type a ticker, and 5 specialized AI subagents research it in parallel: news sentiment, fundamentals, technicals, macro/sector context, and portfolio risk. A synthesis agent then reads all 5 findings, explicitly identifies where they conflict (not just averages them away), and produces one final recommendation, BUY/HOLD/SELL, a confidence level, a bull case and bear case as clear bullet points, key risks, and catalysts to watch. The full reasoning trace is visible, not a black box.

Analyses are cached once per day per ticker (see **Daily Cache** below), so most lookups of an already-searched stock return instantly rather than re-running the full pipeline. And unlike most AI stock tools, TradeDesk publishes its own track record: every recommendation gets checked against what the stock actually did, visible to anyone on the **Track Record** page.

---

## Architecture

```
TradeDesk Orchestrator
        │
        ├── SA1: News & Sentiment    ← live web search (Tavily)
        ├── SA2: Fundamentals        ← Yahoo Finance, SEC EDGAR (US only)
        ├── SA3: Technical           ← price data, RSI, MACD, SMAs, Bollinger Bands,
        │                              200-day SMA / RSI exit signals
        ├── SA4: Macro & Sector      ← sector comparison + live web search
        └── SA5: Portfolio Risk      ← concentration, sector overlap (portfolio mode)
                    │
                    ▼ (all 5 run in parallel, bounded to a 45s time budget —
                      a stuck/slow subagent no longer blocks the other 4)
        Synthesis Agent (Claude Sonnet + Extended Thinking)
                    │
                    ▼
        Recommendation: BUY/HOLD/SELL + confidence + conflicts resolved
                    │
                    ├──→ Persisted to DynamoDB (daily cache + permanent history)
                    │
                    ▼
        Streamlit Web App (candlestick/line chart, quarterly earnings,
        full fundamentals table, extended thinking trace, Track Record page)
```

**Why parallel subagents instead of one big prompt?** Each subagent has an isolated context, its own tools, and its own domain focus, which produces sharper analysis than one model trying to cover five things at once. Running them concurrently also cuts wall-clock time significantly versus doing them one after another. When signals conflict (e.g. strong fundamentals but a bearish technical setup), the synthesis agent reasons through that conflict explicitly with extended thinking, rather than quietly averaging it into a meaningless "neutral."

---

## Where the Models Run: AWS Bedrock

TradeDesk runs on **AWS Bedrock**, not the direct Anthropic API, primarily to run on AWS's free-tier credit. A few things worth knowing:

- It uses Bedrock's **Converse API**, not the raw `InvokeModel` operation, since the latter doesn't reliably support tool use for these models.
- It uses **cross-region inference profiles** (e.g. `us.anthropic.claude-sonnet-4-6`), which some newer Claude models require on Bedrock instead of a raw model ID.
- Switching providers is a single line in `config.py` (`MODEL_PROVIDER = "anthropic"` or `"bedrock"`), every subagent and tool call flows through one abstracted `ModelClient` class that doesn't know or care which provider is active.

```python
MODEL_PROVIDER = "bedrock"   # or "anthropic" to use the direct API instead
```

**One real limitation this creates**: Bedrock has no equivalent of Anthropic's native, server-executed web search tool. That's why News and Macro's live search runs through a custom tool backed by **Tavily** instead, a real function-calling tool that works identically regardless of which provider is active.

---

## Real Data, Not Model Guesswork

| Data | Source | Notes |
|---|---|---|
| Price, fundamentals, valuation ratios | Yahoo Finance (`yfinance`) | Free, no key. Automatically retries and falls back to **Twelve Data** if Yahoo is rate-limited (common on shared cloud hosting) — price history only for now, fundamentals fallback is a planned follow-up |
| SEC filing status | SEC EDGAR | US-listed companies only |
| Live news, current events | Tavily | Free tier: 1,000 searches/month |
| Sector comparison | Yahoo Finance | — |

Only the final synthesis, recommendation, and reasoning are the model's own construction, everything feeding into it is real, retrievable data.

---

## Daily Cache and Traffic Controls

A few upgrades exist specifically to keep the app fast and resilient under real public traffic:

- **Daily analysis cache**: results are cached per ticker, reset at 6 AM US Eastern time. The first request for a stock each day runs the real pipeline; everyone else that day gets the cached result instantly. A visible timestamp shows when it was generated, and a "Force fresh analysis" option bypasses the cache on demand. The live price chart is intentionally **not** part of this cache, it always fetches current data.
- **Persistent, not in-memory**: the cache (and the app's entire analysis history) is backed by **DynamoDB**, not a local file or an in-memory dictionary. Streamlit Community Cloud's own documentation states plainly that local file storage isn't guaranteed to survive a redeploy — DynamoDB does, since the data lives in AWS, not in the app's own filesystem. Runs on DynamoDB's Always-Free tier (25GB storage + provisioned capacity), which never expires, unlike S3's free tier which is 12-months-only.
- **Cache stampede protection**: a per-ticker lock ensures that if several people ask about the same newly-uncached stock at once, only one real analysis runs, not five duplicate ones.
- **Bounded subagent time budget**: the 5 parallel subagents share a 45-second time budget as a group. If one is stuck or slow, the app proceeds with whichever finished, rather than making everyone wait on the slowest one.
- **Token bucket rate limiter**: each browser session gets 5 requests, refilling 1 every 10 minutes. Built primarily as a system-design learning exercise, current traffic doesn't require it, but the pattern (and its honest scope, session-based rather than IP-based) is documented in `src/orchestrator/rate_limiter.py`.

---

## Track Record

A page in the app (📊 Track Record in the sidebar) that publishes real usage and accuracy numbers, not just claims:

- **Usage**: total analyses run and the most-searched tickers, aggregated across everyone who's used the app.
- **Accuracy**: every recommendation old enough to judge (7+ days) gets checked against its real, current price. BUY needs at least a 2% gain to count as correct, SELL needs at least a 2% drop, HOLD needs to have stayed within a 5% band, anything that doesn't clearly satisfy or contradict the call is marked inconclusive rather than forced into right/wrong. This is a transparent directional heuristic, not a quantitative backtest (it doesn't control for the broader market's move over the same period, for instance) — that tradeoff is stated plainly on the page itself.

A clear disclaimer sits at the top of this page: these numbers describe past pattern-matching, not a guarantee of future results.

---

## Continuous Integration

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push:

- **Compile check** (blocking): every Python file in the project must actually compile — pure syntax/import validation, zero network calls, zero API keys needed.
- **Data layer smoke tests** (informational, non-blocking): runs the existing zero-AI-cost test suite against live Yahoo Finance and SEC EDGAR. Deliberately set to never block a push on its own — these depend on external services that can rate-limit CI runners for reasons that have nothing to do with whether the code is actually correct (this project has hit that exact problem on its own hosting before).

---

## Quick Start

```bash
git clone https://github.com/svd009/tradedesk.git
cd tradedesk
pip install -r requirements.txt
cp .env.example .env   # fill in the keys below

streamlit run app.py
```

**Environment variables needed** (see `.env.example`):

| Variable | Required for | Free tier? |
|---|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | Running models via Bedrock (default), and DynamoDB persistence | Yes, AWS signup credit + DynamoDB Always Free tier |
| `ANTHROPIC_API_KEY` | Running models via the direct Anthropic API instead (`MODEL_PROVIDER = "anthropic"`) | No |
| `TAVILY_API_KEY` | Live news search (News, Macro subagents) | Yes, 1,000 searches/month |
| `TWELVE_DATA_API_KEY` | Fallback price data if Yahoo Finance is rate-limited | Yes |
| `DYNAMODB_TABLE_NAME` | Optional — defaults to `tradedesk_analyses` if unset | — |

The app runs fine without `TWELVE_DATA_API_KEY` set (no fallback available if Yahoo fails) or without DynamoDB permissions attached to your IAM user (falls back to an in-memory cache for that session, with a clear log message — see `src/orchestrator/storage.py`).

---

## Project Structure

```
tradedesk/
├── .github/
│   └── workflows/
│       └── ci.yml                   ← GitHub Actions: compile check + smoke tests
├── src/
│   ├── client/
│   │   └── model_client.py          ← provider-agnostic client (Anthropic API / Bedrock Converse)
│   ├── data/
│   │   ├── market_data.py           ← Yahoo Finance + Twelve Data fallback, retry logic
│   │   ├── sec_filings.py           ← SEC EDGAR filing metadata (US only)
│   │   ├── technical_indicators.py  ← RSI, MACD, SMA, Bollinger Bands, support/resistance
│   │   └── ticker_directory.py      ← company name → ticker search (S&P 500 + Nifty 50)
│   ├── mcp_server/
│   │   └── market_tools.py          ← tool schemas + Tavily-backed live search
│   ├── subagents/
│   │   ├── base_agent.py            ← shared agentic loop, tool-use turn handling
│   │   ├── news_agent.py            ← SA1
│   │   ├── fundamentals_agent.py    ← SA2
│   │   ├── technical_agent.py       ← SA3
│   │   ├── macro_agent.py           ← SA4
│   │   └── risk_agent.py            ← SA5
│   ├── orchestrator/
│   │   ├── parallel_runner.py       ← ThreadPoolExecutor spawner, bounded time budget
│   │   ├── synthesis_agent.py       ← Sonnet + extended thinking, conflict resolution
│   │   ├── tradedesk_orchestrator.py ← top-level pipeline coordinator
│   │   ├── analysis_cache.py        ← daily cache logic + cache stampede lock
│   │   ├── storage.py               ← DynamoDB persistence layer (the cache's actual backend)
│   │   ├── accuracy_tracker.py      ← checks past recommendations against real outcomes
│   │   └── rate_limiter.py          ← token bucket rate limiter
│   └── evaluation/
│       └── eval_framework.py        ← completeness, consistency, conflict, structure scoring
├── .streamlit/
│   └── config.toml                  ← hides developer-only toolbar items
├── app.py                           ← Streamlit web app
└── config.py                        ← model/provider selection, cache, rate limit config
```

---

## Built With

- [Anthropic Claude](https://docs.anthropic.com) via **AWS Bedrock** — Claude Haiku (subagents), Claude Sonnet + extended thinking (synthesis)
- [Amazon DynamoDB](https://aws.amazon.com/dynamodb) — persistent daily cache and analysis history
- [yfinance](https://github.com/ranaroussi/yfinance) + [Twelve Data](https://twelvedata.com) — price and fundamental data, with automatic fallback
- [SEC EDGAR](https://www.sec.gov/developer) — free public filing data
- [Tavily](https://tavily.com) — live web search for current news
- [Streamlit](https://streamlit.io) — web interface
- [Plotly](https://plotly.com) — candlestick/line charts with technical overlays
- [boto3](https://boto3.amazonaws.com) — AWS Bedrock and DynamoDB client
- [GitHub Actions](https://github.com/features/actions) — continuous integration

---
