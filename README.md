# TradeDesk: Multi-Subagent Equity Research & Portfolio Intelligence

> **Trading & Fintech Edition** | Built with Claude API — 5 Parallel Subagents, Bedrock-Ready Architecture, Streamlit Web App, Extended Thinking Synthesis

**Status: Complete, deployed, and verified with a real NVDA & TSLA analysis.**

---

## Live Demo

**[Launch TradeDesk →](https://svd009-tradedesk.streamlit.app)**

Enter any ticker and get a full research report in ~20 seconds.

---

## Verified Results; Real NVDA Analysis

| Dimension | Signal | Detail |
|---|---|---|
| News Sentiment | BULLISH | Strong earnings, AI demand narrative |
| Fundamental Health | STRONG (8.2/10) | 85% revenue growth, 63% net margins |
| Technical Signal | BEARISH | Below SMA50, MACD bearish, downtrend |
| Macro Environment | FAVORABLE | AI infrastructure spending cycle intact |
| Portfolio Fit | POOR | 35% existing concentration in a tech-heavy book |

**Final recommendation: HOLD (68% confidence, 6.1/10)**
Synthesis resolved the fundamental vs. technical conflict: trim to 20% position, re-evaluate after technical stabilization above SMA50 or Q2 earnings confirmation.
**Quality score: 10.0/10 PASSED | Completed in 16.8s | Extended thinking: 1,268 chars**

---

## What It Does

TradeDesk runs 5 specialized AI research subagents in parallel on any stock ticker, synthesizes their findings using Claude Sonnet with extended thinking, and delivers a structured investment recommendation with confidence score, bull/bear case, and conflict resolution trace.

It's an equity research assistant, not an autonomous trading bot. The system produces reasoned recommendations; a human decides whether to act.

---

## Architecture

```
TradeDesk Orchestrator
        │
        ├── SA1: News & Sentiment    ← web search, recent events
        ├── SA2: Fundamentals        ← Yahoo Finance, SEC EDGAR
        ├── SA3: Technical           ← price data, RSI, MACD, SMAs
        ├── SA4: Macro & Sector      ← sector ETF comparison, macro context
        └── SA5: Portfolio Risk      ← concentration, sector overlap (portfolio mode)
                    │
                    ▼ (all 5 run in parallel via ThreadPoolExecutor)
        Synthesis Agent (Sonnet + Extended Thinking)
                    │
                    ▼
        Recommendation: BUY/HOLD/SELL + confidence + conflicts resolved
                    │
                    ▼
        Streamlit Web App + JSON Report + Evaluation Score
```

**Why parallel subagents instead of one big prompt?**
Each subagent has an isolated context window, its own tool access, and its own domain focus. Running them concurrently cuts analysis time by ~5x vs sequential chaining. When signals conflict (e.g. strong fundamentals but bearish technicals), the synthesis agent receives independent evidence streams and reasons through the disagreement explicitly using extended thinking — not a single model's surface-level opinion.

---

## Bedrock-Ready Design

The entire system is accessed through a single `ModelClient` abstraction (`src/client/model_client.py`). Switching from the Anthropic API to AWS Bedrock requires changing **one line** in `config.py`:

```python
MODEL_PROVIDER = "anthropic"   # change to "bedrock" for AWS
```

Every agent, every tool call, every synthesis response flows through this single interface — nothing else changes.

---

## Quick Start

```bash
git clone https://github.com/svd009/tradedesk.git
cd tradedesk
pip install -r requirements.txt
cp .env.example .env   # add your Anthropic API key

streamlit run app.py   # launch the web app
```

**Or run the CLI tests:**

```bash
python test_data.py          # Phase 1: data layer (free, no API)
python test_mcp.py           # Phase 2: MCP tools (free, no API)
python test_subagents.py     # Phase 3: 2 subagents (~$0.05)
python test_orchestrator.py  # Phase 4: synthesis (~$0.15)
python test_eval.py          # Phase 5: eval framework (free)
```

---

## Project Structure

```
tradedesk/
├── src/
│   ├── client/
│   │   └── model_client.py          ← Bedrock-ready abstracted client
│   ├── data/
│   │   ├── market_data.py           ← Yahoo Finance price + fundamentals
│   │   ├── sec_filings.py           ← SEC EDGAR filing metadata
│   │   └── technical_indicators.py  ← RSI, MACD, SMA, cross detection
│   ├── mcp_server/
│   │   └── market_tools.py          ← 6 MCP tools + web search
│   ├── subagents/
│   │   ├── base_agent.py            ← shared agentic loop
│   │   ├── news_agent.py            ← SA1
│   │   ├── fundamentals_agent.py    ← SA2
│   │   ├── technical_agent.py       ← SA3
│   │   ├── macro_agent.py           ← SA4
│   │   └── risk_agent.py            ← SA5
│   ├── orchestrator/
│   │   ├── parallel_runner.py       ← ThreadPoolExecutor subagent spawner
│   │   ├── synthesis_agent.py       ← Sonnet + extended thinking
│   │   └── tradedesk_orchestrator.py ← top-level pipeline coordinator
│   └── evaluation/
│       └── eval_framework.py        ← completeness, consistency, conflict, structure
├── reports/                         ← saved JSON research reports
├── app.py                           ← Streamlit web app
└── config.py                        ← model selection, thresholds, Bedrock switch
```

---

## Built With

- [Anthropic Claude API](https://docs.anthropic.com) — claude-haiku-4-5 (subagents), claude-sonnet-4-6 + extended thinking (synthesis)
- [yfinance](https://github.com/ranaroussi/yfinance) — Yahoo Finance price and fundamental data
- [SEC EDGAR API](https://www.sec.gov/developer) — free public filing data
- [Streamlit](https://streamlit.io) — web interface
- [Plotly](https://plotly.com) — price charts
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — tool protocol
- [boto3](https://boto3.amazonaws.com) — AWS Bedrock (ready to activate)

---
