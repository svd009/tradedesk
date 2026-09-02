# Changelog

All notable changes to TradeDesk, dated and append-only. Small fixes and internal refactors are folded into the entry for the feature they supported rather than listed individually, this is meant to be a readable record of what changed and why, not a mirror of the commit log.

Inspired directly by [TickerWorth](https://tickerworth.com)'s own public changelog, a transparency mechanism most consumer AI tools don't bother with.

---

## 2026-09-01

- **Added confidence calibration reporting.** Checks whether the AI's stated confidence percentage actually correlates with real outcomes, buckets past recommendations by their stated confidence and compares each bucket's real accuracy against what that confidence level implies.
- **Added the Scorecard.** Four rule-based 0-10 scores (Performance, Valuation, Growth, Profitability), computed directly from real data, not another AI call. Inspired by Tickertape's own Scorecard feature.
- **Added a live "what if growth were different?" slider** on the Scorecard, recomputes Growth and Valuation scores instantly against a different growth assumption, TickerWorth's WACC-slider idea applied to a formula TradeDesk can actually recompute for free.
- **Added a "not enough data" refusal.** When a majority of the 5 subagents genuinely fail, the app now says so plainly instead of showing a HOLD recommendation that looks confident but isn't backed by real research.
- Investment-advice disclaimer now shows on every analysis result (single-stock and portfolio), not only the Track Record page.

## 2026-08-31

- Documentation pass: README and internal cost notes updated to reflect the AWS Bedrock migration and the current architecture.

## 2026-08-30

- **Migrated the daily analysis cache to DynamoDB**, replacing an in-memory dictionary. Streamlit Community Cloud doesn't guarantee local storage survives a redeploy; DynamoDB does, and its free tier never expires.
- **Added the Track Record page**: public usage stats and accuracy tracking, every recommendation old enough to judge gets checked against its real, current price.
- **Added company-name search**, 554 companies sourced from the S&P 500 and Nifty 50 index constituent lists, for anyone who doesn't know the exact ticker.
- **Added basic CI** (GitHub Actions): a blocking compile check on every push, plus informational data-layer smoke tests.

## 2026-08-29

- **Added exit-focused technical signals**: the 200-day SMA and RSI are now shown as explicit numbers (not just vague labels), with dedicated reasoning on whether they suggest holding or exiting an existing position.

## 2026-08-22 – 2026-08-25

- **Added a Yahoo Finance → Twelve Data fallback** for price history, with retry logic for transient rate-limit failures.
- **Added a token bucket rate limiter**, per browser session.
- Added the first version of the daily analysis cache (later migrated to DynamoDB, see 2026-08-30).

## 2026-08-18 – 2026-08-21

- **Migrated the model provider from the direct Anthropic API to AWS Bedrock**, using the Converse API and cross-region inference profiles (the raw `InvokeModel` operation didn't reliably support tool use for these models).
- **Added live news search via Tavily**, replacing Anthropic's native web search tool, which has no equivalent on Bedrock.
- Added the interactive candlestick/line price chart with technical indicator overlays.
- Added ticker validation, rejecting invalid tickers and unsupported markets before running the expensive analysis pipeline.
- Added multi-currency support (USD, INR, and others) throughout the app.

## 2026-07-02 – 2026-07-17

- **Initial build**: the 5-subagent architecture (News, Fundamentals, Technical, Macro, Risk), the parallel orchestrator, the synthesis agent with extended thinking, the evaluation framework, and the first version of the Streamlit app.
