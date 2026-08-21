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
from plotly.subplots import make_subplots
import pandas as pd
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.orchestrator.tradedesk_orchestrator import TradeDesk
from src.evaluation.eval_framework import TradeDeskevaluator
from src.data.market_data import get_price_history
from src.data.technical_indicators import (
    run_full_technical_analysis, compute_sma_series,
    compute_bollinger_bands, compute_support_resistance,
)
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
            placeholder="US: NVDA, AAPL — India: RELIANCE.NS, TCS.BO",
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

# Common currency codes yfinance returns via stock.info["currency"], mapped
# to their display symbol. Falls back to the code itself (e.g. "SEK") for
# anything not in this list, rather than silently assuming USD.
_CURRENCY_SYMBOLS = {
    "USD": "$", "INR": "₹", "GBP": "£", "EUR": "€", "JPY": "¥",
    "CNY": "¥", "HKD": "HK$", "KRW": "₩", "CAD": "C$", "AUD": "A$",
    "SGD": "S$", "CHF": "CHF ", "BRL": "R$",
}


def is_supported_ticker(ticker: str) -> bool:
    """
    TradeDesk currently supports US tickers (no suffix, e.g. AAPL) and
    Indian NSE/BSE tickers (.NS or .BO suffix, e.g. RELIANCE.NS). Anything
    else (other .XX exchange suffixes) is rejected with a clear message
    rather than silently returning wrong or empty data.
    """
    ticker = ticker.upper().strip()
    if "." not in ticker:
        return True  # no suffix — treated as a US ticker
    return ticker.endswith(".NS") or ticker.endswith(".BO")


def currency_symbol(currency_code: str) -> str:
    return _CURRENCY_SYMBOLS.get(currency_code, f"{currency_code} " if currency_code else "$")


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


def price_chart(ticker, chart_type="Candlestick", overlays=None, show_volume=True):
    """
    Build the price chart.

    chart_type: "Candlestick" or "Line"
    overlays:   subset of {"SMA 20", "SMA 50", "SMA 200", "Bollinger Bands",
                "Support/Resistance"} — the most commonly used technical
                markers, kept as an explicit opt-in list so the chart
                doesn't get cluttered by default.
    show_volume: whether to add a volume bar panel below the price panel.
    """
    overlays = overlays or []
    price_data = get_price_history(ticker, days=180)
    if price_data.get("error") or not price_data.get("closes"):
        return None

    dates = pd.to_datetime(price_data["dates"])
    closes = price_data["closes"]
    opens = price_data.get("opens", closes)
    highs = price_data.get("highs", closes)
    lows = price_data.get("lows", closes)
    volumes = price_data.get("volumes", [])
    curr = currency_symbol(price_data.get("currency", "USD"))

    rows = 2 if show_volume else 1
    row_heights = [0.75, 0.25] if show_volume else [1.0]
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        row_heights=row_heights, vertical_spacing=0.03,
    )

    # ── Price panel: candlestick or line ────────────────────────────
    if chart_type == "Candlestick":
        fig.add_trace(go.Candlestick(
            x=dates, open=opens, high=highs, low=lows, close=closes,
            name=ticker, increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
            showlegend=False,
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=dates, y=closes, name=ticker, line=dict(color="#2563eb", width=2),
            hovertemplate="%{x|%b %d}<br>" + curr + "%{y:.2f}<extra></extra>", showlegend=False,
        ), row=1, col=1)

    # ── Overlays: real rolling series, not flat single-value lines ──
    sma_colors = {"SMA 20": "#7c3aed", "SMA 50": "#f59e0b", "SMA 200": "#ef4444"}
    for label, color in sma_colors.items():
        if label in overlays:
            period = int(label.split()[-1])
            series = compute_sma_series(closes, period)
            fig.add_trace(go.Scatter(
                x=dates, y=series, name=label, line=dict(color=color, width=1.3),
                hovertemplate=f"{label}: {curr}" + "%{y:.2f}<extra></extra>",
            ), row=1, col=1)

    if "Bollinger Bands" in overlays:
        bb = compute_bollinger_bands(closes, period=20, num_std=2.0)
        fig.add_trace(go.Scatter(
            x=dates, y=bb["upper"], name="Bollinger Upper",
            line=dict(color="#94a3b8", width=1, dash="dot"),
            hovertemplate="Upper: " + curr + "%{y:.2f}<extra></extra>",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=dates, y=bb["lower"], name="Bollinger Lower",
            line=dict(color="#94a3b8", width=1, dash="dot"),
            fill="tonexty", fillcolor="rgba(148,163,184,0.08)",
            hovertemplate="Lower: " + curr + "%{y:.2f}<extra></extra>",
        ), row=1, col=1)

    if "Support/Resistance" in overlays:
        sr = compute_support_resistance(closes)
        if sr.get("support"):
            fig.add_hline(y=sr["support"], line_dash="dash", line_color="#16a34a",
                          annotation_text="Support", row=1, col=1)
        if sr.get("resistance"):
            fig.add_hline(y=sr["resistance"], line_dash="dash", line_color="#dc2626",
                          annotation_text="Resistance", row=1, col=1)

    # ── Volume panel ──────────────────────────────────────────────
    if show_volume and volumes:
        bar_colors = [
            "#16a34a" if closes[i] >= (opens[i] if i < len(opens) else closes[i-1] if i > 0 else closes[i])
            else "#dc2626"
            for i in range(len(closes))
        ]
        fig.add_trace(go.Bar(
            x=dates, y=volumes, name="Volume", marker_color=bar_colors,
            showlegend=False, hovertemplate="Vol: %{y:,}<extra></extra>",
        ), row=2, col=1)

    fig.update_layout(
        height=440 if show_volume else 340,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=bool(overlays),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0", row=1, col=1)
    if show_volume:
        fig.update_yaxes(showgrid=False, row=2, col=1)
    return fig



# Fields that hold the plain-English writeup for a subagent finding.
_SUMMARY_FIELDS = ("summary", "rationale")
# Fields that are just bookkeeping, not shown to the user.
_HIDDEN_FIELDS = ("agent", "parse_error", "raw_output", "ticker", "company_name")
# Fields that read best as bullet lists (they're lists of short strings).
_LIST_FIELDS = ("key_events", "key_strengths", "key_risks", "tailwinds",
                "headwinds", "key_macro_risks", "recent_catalysts")


def render_subagent_finding(data: dict, currency: str = "$"):
    """
    Render one subagent's finding as readable prose and signal pills,
    instead of dumping the raw JSON the model returned.
    """
    # 1. The plain-English writeup goes first and reads like actual research.
    for field in _SUMMARY_FIELDS:
        if data.get(field):
            st.markdown(f"> {data[field]}")

    # 2. Status-style fields (single string values, not lists/dicts) as pills.
    status_fields = {
        k: v for k, v in data.items()
        if k not in _HIDDEN_FIELDS and k not in _SUMMARY_FIELDS
        and k not in _LIST_FIELDS and k != "confidence" and k != "key_levels"
        and isinstance(v, (str, bool)) and v is not None
    }
    if status_fields:
        pills = " &nbsp; ".join(
            f"**{k.replace('_', ' ').title()}:** {signal_pill(k, v)}"
            for k, v in status_fields.items()
        )
        st.markdown(pills, unsafe_allow_html=True)

    # 3. Numeric/price levels (e.g. technical support & resistance) as metrics.
    if isinstance(data.get("key_levels"), dict):
        levels = data["key_levels"]
        cols = st.columns(len(levels))
        for col, (label, val) in zip(cols, levels.items()):
            col.metric(label.replace("_", " ").title(), f"{currency}{val:,.2f}" if isinstance(val, (int, float)) else val)

    # 4. List fields as actual bullet points, not JSON arrays.
    for field in _LIST_FIELDS:
        items = data.get(field)
        if items:
            st.markdown(f"**{field.replace('_', ' ').title()}**")
            for item in items:
                if isinstance(item, dict):
                    # e.g. news key_events: [{"event": ..., "impact": ..., "materiality": ...}]
                    label = item.get("event") or item.get("headline") or str(item)
                    extra = " · ".join(
                        str(v) for k, v in item.items() if k not in ("event", "headline")
                    )
                    st.markdown(f"- {label}" + (f" _({extra})_" if extra else ""))
                else:
                    st.markdown(f"- {item}")

    # 5. Confidence as a simple progress indicator.
    if isinstance(data.get("confidence"), (int, float)):
        st.progress(min(max(data["confidence"], 0.0), 1.0),
                     text=f"Model confidence: {data['confidence']:.0%}")


def render_single_stock_result(result):
    rec_data = result["synthesis"]["recommendation"]
    ticker = result["ticker"]
    company = result.get("company_name", ticker)
    rec = rec_data.get("recommendation", "HOLD")
    confidence = rec_data.get("confidence", 0)
    score = rec_data.get("composite_score", 5)
    curr = currency_symbol(get_price_history(ticker, days=180).get("currency", "USD"))

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
        chart_controls = st.columns([1, 2])
        with chart_controls[0]:
            chart_type = st.radio(
                "View", ["Line", "Candlestick"], horizontal=True,
                key=f"chart_type_{ticker}", label_visibility="collapsed",
            )
        with chart_controls[1]:
            overlays = st.multiselect(
                "Indicators", ["SMA 20", "SMA 50", "SMA 200", "Bollinger Bands", "Support/Resistance"],
                default=[], key=f"overlays_{ticker}",
                label_visibility="collapsed", placeholder="Add indicators…",
            )
        show_volume = st.checkbox("Show volume", value=True, key=f"vol_{ticker}")
        fig = price_chart(ticker, chart_type=chart_type, overlays=overlays, show_volume=show_volume)
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

    def render_case(items):
        """
        bull_case/bear_case are now a list of points. Handles the old
        paragraph-string format too, in case a result from before this
        change is still in session state, so it never renders blank.
        """
        if isinstance(items, str):
            return items  # old format — show as-is rather than breaking
        return "\n".join(f"- {point}" for point in items) if items else ""

    col_bull, col_bear = st.columns(2)
    with col_bull:
        st.success(f"**Bull Case**\n\n{render_case(rec_data.get('bull_case', []))}")
    with col_bear:
        st.error(f"**Bear Case**\n\n{render_case(rec_data.get('bear_case', []))}")

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
                    render_subagent_finding(data, currency=curr)
                else:
                    st.caption("No data available")

    # ── Extended thinking ─────────────────────────────────────────
    thinking = result["synthesis"].get("thinking", "")
    if thinking:
        with st.expander(f"🧠 Extended Thinking Trace ({len(thinking):,} chars)"):
            snippet = thinking[:2000]
            for paragraph in snippet.split("\n\n"):
                if paragraph.strip():
                    st.write(paragraph.strip())
            if len(thinking) > 2000:
                st.caption("… truncated, download the full report for the complete trace")

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

# st.button() only returns True for the single rerun triggered by the click
# itself — any later rerun (e.g. toggling the chart's Line/Candlestick radio)
# sees run_button as False again. Without storing the result, that would
# discard the whole analysis and drop back to the landing page. Session state
# keeps the last result alive across those reruns, and a fresh click only
# happens when run_button is actually True, so chart interactions never
# re-trigger the (expensive) analysis.
if "last_result" not in st.session_state:
    st.session_state.last_result = None
    st.session_state.last_mode = None

if run_button:
    if mode == "Single Stock":
        if not ticker_input:
            st.error("Please enter a ticker symbol.")
            st.stop()
        if not is_supported_ticker(ticker_input):
            st.error(
                "TradeDesk currently supports **US** stocks (e.g. `AAPL`, `NVDA`) "
                "and **Indian** stocks on NSE/BSE (e.g. `RELIANCE.NS`, `TCS.BO`) only. "
                "Other exchanges aren't supported yet."
            )
            st.stop()

        # Confirm this is an actual, tradeable ticker BEFORE spending any AI
        # API calls or search credits on it. Without this check, typing a
        # person's name (or any garbage) still ran the full 5-agent +
        # synthesis pipeline, burning real cost and cycles, and the models
        # would sometimes fabricate a confident-looking recommendation from
        # zero real data instead of clearly failing. This check costs one
        # cheap Yahoo Finance lookup, not an AI call.
        with st.spinner(f"Checking that {ticker_input} is a real ticker..."):
            precheck = get_price_history(ticker_input, days=5)
        if precheck.get("error") or not precheck.get("closes"):
            st.error(
                f"**\"{ticker_input}\"** doesn't match a real, tradeable stock. "
                "Double-check the ticker symbol, for Indian stocks remember the "
                "`.NS` or `.BO` suffix, e.g. `RELIANCE.NS`."
            )
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
                st.session_state.last_result = result
                st.session_state.last_mode = "single"
            except Exception as e:
                st.error(f"Analysis failed: {str(e)}")
                st.exception(e)

    else:
        if not portfolio_input:
            st.error("Please enter at least one holding.")
            st.stop()
        unsupported = [t for t in portfolio_input if not is_supported_ticker(t)]
        if unsupported:
            st.error(
                f"These tickers aren't from a supported market: {', '.join(unsupported)}. "
                "TradeDesk currently supports US stocks and Indian NSE/BSE stocks "
                "(`.NS` / `.BO` suffix) only."
            )
            st.stop()

        with st.spinner("Checking that all tickers are real..."):
            precheck_results = {t: get_price_history(t, days=5) for t in portfolio_input}
            invalid = [
                t for t, data in precheck_results.items()
                if data.get("error") or not data.get("closes")
            ]
        if invalid:
            st.error(
                f"These don't match real, tradeable stocks: {', '.join(invalid)}. "
                "Double-check the tickers before running the analysis."
            )
            st.stop()

        with st.spinner("Analyzing portfolio..."):
            try:
                td = TradeDesk()
                result = td.analyze_portfolio(
                    portfolio=portfolio_input,
                    verbose=False,
                )
                st.session_state.last_result = result
                st.session_state.last_mode = "portfolio"
            except Exception as e:
                st.error(f"Analysis failed: {str(e)}")
                st.exception(e)

# ── Render whatever the last completed analysis was ─────────────────────────
# This runs on every rerun, including ones triggered by chart controls, so
# those interactions redraw instantly from the stored result instead of
# re-running the 5-subagent pipeline or losing the result entirely.
if st.session_state.last_result is not None:
    if st.session_state.last_mode == "single":
        render_single_stock_result(st.session_state.last_result)
    else:
        render_portfolio_result(st.session_state.last_result)
else:
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
