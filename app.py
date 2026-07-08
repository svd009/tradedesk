"""
app.py
───────
TradeDesk Streamlit Web Application.

Single stock mode:  Enter a ticker → get a full research report
Portfolio mode:     Enter holdings → analyze all positions

Run with: streamlit run app.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.orchestrator.tradedesk_orchestrator import TradeDesk
from src.evaluation.eval_framework import TradeDeskevaluator
from src.data.market_data import get_price_history
from src.data.technical_indicators import run_full_technical_analysis
from config import DEMO_PORTFOLIO

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TradeDesk",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.rec-badge {
    display: inline-block;
    padding: 8px 20px;
    border-radius: 8px;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.05em;
}
.STRONG_BUY  { background: #1a7340; color: white; }
.BUY         { background: #2d9e5f; color: white; }
.HOLD        { background: #a67c00; color: white; }
.SELL        { background: #c0392b; color: white; }
.STRONG_SELL { background: #7b241c; color: white; }
.signal-pill {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    margin: 2px;
}
.bullish  { background: #d5f5e3; color: #1a7340; }
.bearish  { background: #fadbd8; color: #922b21; }
.neutral  { background: #fdebd0; color: #935116; }
.na       { background: #f0f0f0; color: #666; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 TradeDesk")
    st.caption("Multi-Subagent Equity Research System")
    st.divider()

    mode = st.radio("Analysis Mode", ["Single Stock", "Portfolio"], index=0)
    st.divider()

    if mode == "Single Stock":
        ticker_input = st.text_input(
            "Ticker Symbol",
            value="NVDA",
            placeholder="e.g. NVDA, AAPL, TSLA",
        ).upper().strip()
        include_portfolio_context = st.checkbox(
            "Include portfolio context (SA5)",
            value=False,
            help="Analyzes how this stock fits the demo portfolio"
        )
        portfolio_for_analysis = DEMO_PORTFOLIO if include_portfolio_context else None

    else:
        st.subheader("Portfolio Holdings")
        st.caption("Enter ticker and weight (%) for each holding")

        portfolio_input = {}
        default_tickers = list(DEMO_PORTFOLIO.keys())
        default_weights = [int(w * 100) for w in DEMO_PORTFOLIO.values()]

        for i in range(5):
            col1, col2 = st.columns([2, 1])
            with col1:
                t = st.text_input(
                    f"Ticker {i+1}",
                    value=default_tickers[i] if i < len(default_tickers) else "",
                    key=f"ticker_{i}",
                    label_visibility="collapsed",
                    placeholder=f"Ticker {i+1}",
                )
            with col2:
                w = st.number_input(
                    f"Weight {i+1}",
                    min_value=0, max_value=100,
                    value=default_weights[i] if i < len(default_weights) else 0,
                    key=f"weight_{i}",
                    label_visibility="collapsed",
                )
            if t and w > 0:
                portfolio_input[t.upper()] = w / 100

    st.divider()
    run_button = st.button(
        "🔍 Run Analysis" if mode == "Single Stock" else "🔍 Analyze Portfolio",
        type="primary",
        use_container_width=True,
    )

    st.divider()
    st.caption("Built with Claude API · Multi-subagent architecture · Bedrock-ready")


# ── Helper functions ──────────────────────────────────────────────────────────

def signal_pill(label, value):
    if value is None:
        return f'<span class="signal-pill na">N/A</span>'
    v = str(value).upper()
    if any(x in v for x in ("BULLISH", "STRONG", "FAVORABLE", "ACCELERATING",
                              "CHEAP", "EXCELLENT", "GOOD", "OUTPERFORM",
                              "BUY", "UPTREND")):
        css = "bullish"
    elif any(x in v for x in ("BEARISH", "WEAK", "UNFAVORABLE", "DECELERATING",
                                "EXPENSIVE", "POOR", "UNDERPERFORM",
                                "SELL", "DOWNTREND", "NEGATIVE")):
        css = "bearish"
    else:
        css = "neutral"
    return f'<span class="signal-pill {css}">{value}</span>'


def rec_color(rec):
    colors = {
        "STRONG_BUY": "#1a7340", "BUY": "#2d9e5f",
        "HOLD": "#a67c00",
        "SELL": "#c0392b", "STRONG_SELL": "#7b241c",
    }
    return colors.get(rec, "#555")


def price_chart(ticker):
    price_data = get_price_history(ticker, days=180)
    if price_data.get("error") or not price_data.get("closes"):
        return None
    df = pd.DataFrame({"date": price_data["dates"], "price": price_data["closes"]})
    df["date"] = pd.to_datetime(df["date"])
    tech = run_full_technical_analysis(price_data)
    mas = tech.get("moving_averages", {})

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["price"],
        name=ticker, line=dict(color="#2563eb", width=2),
        hovertemplate="%{x|%b %d}<br>$%{y:.2f}<extra></extra>",
    ))
    if mas.get("sma50"):
        fig.add_hline(y=mas["sma50"], line_dash="dash",
                      line_color="#f59e0b", annotation_text="SMA50")
    if mas.get("sma200"):
        fig.add_hline(y=mas["sma200"], line_dash="dot",
                      line_color="#ef4444", annotation_text="SMA200")
    fig.update_layout(
        height=320, margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
    )
    return fig


def render_single_stock_result(result):
    rec_data = result["synthesis"]["recommendation"]
    ticker = result["ticker"]
    company = result.get("company_name", ticker)
    rec = rec_data.get("recommendation", "HOLD")
    confidence = rec_data.get("confidence", 0)
    score = rec_data.get("composite_score", 5)

    # ── Header ────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.markdown(f"### {company} ({ticker})")
        st.caption(result.get("sector", ""))
    with col2:
        st.markdown(
            f'<div class="rec-badge {rec}">{rec.replace("_", " ")}</div>',
            unsafe_allow_html=True
        )
    with col3:
        st.metric("Confidence", f"{confidence:.0%}")
        st.metric("Composite Score", f"{score}/10")

    st.divider()

    # ── Price chart + signal summary ──────────────────────────────
    col_chart, col_signals = st.columns([3, 2])
    with col_chart:
        st.subheader("Price History (6M)")
        fig = price_chart(ticker)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("Price chart unavailable")

    with col_signals:
        st.subheader("Signal Summary")
        ss = rec_data.get("signal_summary", {})
        signals = [
            ("News Sentiment",      ss.get("news_sentiment")),
            ("Fundamental Health",  ss.get("fundamental_health")),
            ("Technical Signal",    ss.get("technical_signal")),
            ("Macro Environment",   ss.get("macro_environment")),
            ("Portfolio Fit",       ss.get("portfolio_fit")),
        ]
        for label, val in signals:
            st.markdown(
                f"**{label}** {signal_pill(label, val)}",
                unsafe_allow_html=True,
            )
            st.write("")

    st.divider()

    # ── Executive summary + bull/bear ─────────────────────────────
    st.subheader("Executive Summary")
    st.info(rec_data.get("executive_summary", ""))

    col_bull, col_bear = st.columns(2)
    with col_bull:
        st.success(f"**Bull Case**\n\n{rec_data.get('bull_case', '')}")
    with col_bear:
        st.error(f"**Bear Case**\n\n{rec_data.get('bear_case', '')}")

    # ── Risks + catalysts ─────────────────────────────────────────
    col_risk, col_cat = st.columns(2)
    with col_risk:
        st.subheader("Key Risks")
        for r in rec_data.get("key_risks", []):
            st.markdown(f"- {r}")
    with col_cat:
        st.subheader("Catalysts to Watch")
        for c in rec_data.get("catalysts_to_watch", []):
            st.markdown(f"- {c}")

    # ── Conflict resolution ───────────────────────────────────────
    conflicts = rec_data.get("key_conflicts", [])
    if conflicts:
        with st.expander("⚖️ Signal Conflicts Resolved by Synthesis Agent"):
            for c in conflicts:
                st.markdown(f"- {c}")

    # ── Subagent detail ───────────────────────────────────────────
    with st.expander("🔬 Subagent Research Detail"):
        findings = result["subagent_findings"]
        tabs = st.tabs(["📰 News", "📊 Fundamentals", "📈 Technical", "🌍 Macro", "⚖️ Risk"])
        agent_keys = ["news", "fundamentals", "technical", "macro", "risk"]
        for tab, key in zip(tabs, agent_keys):
            with tab:
                data = findings.get(key, {})
                if data:
                    # Show clean subset of fields
                    display = {k: v for k, v in data.items()
                                if k not in ("agent", "parse_error", "raw_output")
                                and v is not None}
                    st.json(display)
                else:
                    st.caption("No data available")

    # ── Extended thinking ─────────────────────────────────────────
    thinking = result["synthesis"].get("thinking", "")
    if thinking:
        with st.expander(f"🧠 Extended Thinking Trace ({len(thinking):,} chars)"):
            st.markdown(f"```\n{thinking[:2000]}{'...' if len(thinking) > 2000 else ''}\n```")

    # ── Evaluation ────────────────────────────────────────────────
    evaluator = TradeDeskevaluator()
    eval_result = evaluator.evaluate(
        result["synthesis"], result["subagent_findings"], verbose=False
    )
    with st.expander(f"✅ Synthesis Quality Score: {eval_result['overall_score']}/10 "
                     f"({'PASSED' if eval_result['passed'] else 'BELOW THRESHOLD'})"):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Completeness",        f"{eval_result['completeness_score']}/10")
        col2.metric("Consistency",         f"{eval_result['consistency_score']}/10")
        col3.metric("Conflict Resolution", f"{eval_result['conflict_score']}/10")
        col4.metric("Structure",           f"{eval_result['structure_score']}/10")

    # ── Download report ───────────────────────────────────────────
    report_json = json.dumps(result["report"], indent=2, default=str)
    st.download_button(
        "⬇️ Download Full Report (JSON)",
        data=report_json,
        file_name=f"tradedesk_{ticker}_{result['report']['report_id']}.json",
        mime="application/json",
    )
    elapsed = result["subagent_findings"].get("elapsed_seconds", 0)
    st.caption(f"Analysis completed in {elapsed}s · "
               f"5 subagents ran in parallel · "
               f"Synthesis used extended thinking")


def render_portfolio_result(result):
    st.subheader("Portfolio Analysis Summary")
    recs = result["recommendations"]
    avg = result["portfolio_avg_score"]

    # Summary table
    rows = []
    for ticker, rec in recs.items():
        ind_result = result["individual_results"][ticker]
        rec_data = ind_result["synthesis"]["recommendation"]
        rows.append({
            "Ticker": ticker,
            "Recommendation": rec,
            "Score": rec_data.get("composite_score", 5),
            "Confidence": f"{rec_data.get('confidence', 0):.0%}",
            "Sector": ind_result.get("sector", ""),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.metric("Portfolio Average Score", f"{avg}/10")
    st.divider()

    for ticker in recs:
        with st.expander(f"{ticker} — {recs[ticker]}"):
            render_single_stock_result(result["individual_results"][ticker])


# ── Main app ──────────────────────────────────────────────────────────────────
st.title("📈 TradeDesk")
st.caption("Multi-Subagent Equity Research & Portfolio Intelligence · Powered by Claude API")

if not run_button:
    # Landing state
    st.info(
        "Enter a ticker in the sidebar and click **Run Analysis** to get started.\n\n"
        "TradeDesk runs 5 specialized research subagents in parallel — "
        "news sentiment, fundamentals, technical analysis, macro context, and portfolio risk — "
        "then synthesizes them using Claude Sonnet with extended thinking."
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("Subagents", "5", "running in parallel")
    col2.metric("Model", "Claude API", "Bedrock-ready")
    col3.metric("Data sources", "3 free", "Yahoo Finance + SEC EDGAR + web")

else:
    # Run the analysis
    if mode == "Single Stock":
        if not ticker_input:
            st.error("Please enter a ticker symbol.")
            st.stop()

        status_container = st.empty()
        progress_bar = st.progress(0)
        agent_statuses = {}

        def update_status(agent_name, status):
            agent_statuses[agent_name] = status
            lines = "\n".join(f"- **{k}**: {v}" for k, v in agent_statuses.items())
            status_container.markdown(f"**Running subagents...**\n\n{lines}")
            done = sum(1 for v in agent_statuses.values() if "✓" in str(v))
            progress_bar.progress(min(done / 5, 1.0))

        with st.spinner(f"Analyzing {ticker_input}..."):
            try:
                td = TradeDesk()
                result = td.analyze(
                    ticker=ticker_input,
                    portfolio=portfolio_for_analysis,
                    verbose=False,
                    status_callback=update_status,
                )
                status_container.empty()
                progress_bar.empty()
                render_single_stock_result(result)
            except Exception as e:
                st.error(f"Analysis failed: {str(e)}")
                st.exception(e)

    else:
        if not portfolio_input:
            st.error("Please enter at least one holding.")
            st.stop()

        with st.spinner("Analyzing portfolio..."):
            try:
                td = TradeDesk()
                result = td.analyze_portfolio(
                    portfolio=portfolio_input,
                    verbose=False,
                )
                render_portfolio_result(result)
            except Exception as e:
                st.error(f"Analysis failed: {str(e)}")
                st.exception(e)
