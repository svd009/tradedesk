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
from src.orchestrator import analysis_cache
from src.orchestrator import accuracy_tracker
from src.orchestrator.rate_limiter import get_user_bucket
from src.evaluation.eval_framework import TradeDeskevaluator
from src.data.market_data import get_price_history, get_fundamentals
from src.data.ticker_directory import COMPANY_TO_TICKER
from src.data.technical_indicators import (
    run_full_technical_analysis, compute_sma_series,
    compute_bollinger_bands, compute_support_resistance,
)
from config import DEMO_PORTFOLIO, PRICE_HISTORY_DAYS, RATE_LIMIT_BUCKET_CAPACITY, RATE_LIMIT_REFILL_SECONDS_PER_TOKEN

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

    mode = st.radio("Analysis Mode", ["Single Stock", "Portfolio", "📊 Track Record"], index=0)
    st.divider()

    run_button = False  # only set for real in the form below — Track
                        # Record mode has no form/button of its own,
                        # it just renders immediately when selected

    if mode == "📊 Track Record":
        st.caption("See how many analyses have run, and how the "
                   "recommendations have actually held up so far.")

    elif mode == "Single Stock":
        # Outside the form on purpose — widgets inside st.form don't fire
        # on_change until the form is submitted, but we want selecting a
        # company here to update the ticker field immediately, before the
        # user even clicks Run Analysis.
        company_options = sorted(COMPANY_TO_TICKER.keys())

        def _apply_company_selection():
            selected = st.session_state.get("_company_search_select")
            if selected:
                st.session_state["ticker_symbol_input"] = COMPANY_TO_TICKER[selected]

        st.selectbox(
            "Don't know the ticker? Search by company name",
            options=company_options,
            index=None,  # no option pre-selected — shows `placeholder` below
                         # instead, styled light/faded by Streamlit itself,
                         # rather than a fake option sitting in the list
            placeholder="🔍 Search by company name...",
            key="_company_search_select",
            on_change=_apply_company_selection,
            help="Selecting a company here fills in the ticker field below. "
                 "You can still type a ticker directly if it's not in this list.",
        )

    # Track Record mode has no form, no ticker input, no run button —
    # it's a read-only view that renders immediately below once selected.
    if mode != "📊 Track Record":
        # Wrapped in a form so pressing Enter in the ticker field submits,
        # same as clicking the button — st.text_input alone doesn't do this,
        # only a form's submit button (and Enter within it) does.
        with st.form("analysis_form"):
            if mode == "Single Stock":
                ticker_input = st.text_input(
                    "Ticker Symbol",
                    value="",
                    key="ticker_symbol_input",
                    placeholder="Add ticker here (e.g. NVDA, AAPL, RELIANCE.NS)",
                ).upper().strip()
                include_portfolio_context = st.checkbox(
                    "Include portfolio context (SA5)",
                    value=False,
                    help="Analyzes how this stock fits the demo portfolio"
                )
                portfolio_for_analysis = DEMO_PORTFOLIO if include_portfolio_context else None
                force_refresh = st.checkbox(
                    "Force fresh analysis",
                    value=False,
                    help="Analyses are cached once per day (resets 6 AM ET) for speed. "
                         "Check this to run a brand-new analysis right now instead."
                )
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
            run_button = st.form_submit_button(
                "🔍 Run Analysis" if mode == "Single Stock" else "🔍 Analyze Portfolio",
                type="primary",
                use_container_width=True,
            )

    st.divider()
    # NOTE (not shown in UI): "Built with Claude API · Multi-subagent
    # architecture · Bedrock-ready" used to appear here as a sidebar caption.


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
    price_data = get_price_history(ticker, days=PRICE_HISTORY_DAYS)
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
        dragmode="pan",  # click-and-drag scrolls the chart directly, no toolbar needed
    )
    fig.update_xaxes(showgrid=False)
    # Show the most recent 180 days by default — the full ~20 months of
    # fetched history (PRICE_HISTORY_DAYS) is still in the chart's data,
    # just outside the initial view. Dragging left reveals it.
    if len(dates) > 0:
        default_window_start = dates.max() - pd.Timedelta(days=180)
        fig.update_xaxes(range=[default_window_start, dates.max()])
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0", row=1, col=1)
    if show_volume:
        fig.update_yaxes(showgrid=False, row=2, col=1)
    return fig



# Fields that hold the plain-English writeup for a subagent finding.
_SUMMARY_FIELDS = ("summary", "rationale", "exit_signal_notes")
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
    # Fields that are index/ratio numbers, not prices — no currency prefix.
    _NON_CURRENCY_LEVELS = {"rsi"}
    # Labels that need finance-standard capitalization rather than
    # generic title-casing ("Sma 200" -> "200-Day SMA").
    _LEVEL_LABEL_OVERRIDES = {"sma_200": "200-Day SMA", "sma_50": "50-Day SMA", "sma_20": "20-Day SMA", "rsi": "RSI"}

    if isinstance(data.get("key_levels"), dict):
        levels = data["key_levels"]
        cols = st.columns(len(levels))
        for col, (label, val) in zip(cols, levels.items()):
            display_label = _LEVEL_LABEL_OVERRIDES.get(label, label.replace("_", " ").title())
            if isinstance(val, (int, float)):
                display_val = f"{val:,.2f}" if label in _NON_CURRENCY_LEVELS else f"{currency}{val:,.2f}"
            else:
                display_val = val
            col.metric(display_label, display_val)

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


def _pct(value):
    """Format a decimal (0.63) as a percentage string (63.0%), handling None."""
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "N/A"


def _num(value, decimals=2):
    return f"{value:.{decimals}f}" if isinstance(value, (int, float)) else "N/A"


def render_quarterly_and_fundamentals(ticker: str, currency: str = "$"):
    """
    Real quarterly earnings and fundamental ratios, straight from Yahoo
    Finance. Shown separately from the Fundamentals agent's own narrative
    summary so the actual numbers are guaranteed accurate rather than
    depending on the model to transcribe them correctly in prose.
    """
    fund = get_fundamentals(ticker)
    if fund.get("error"):
        st.caption("Fundamental data unavailable for this ticker.")
        return

    st.markdown("**Recent Quarterly Earnings (EPS)**")
    earnings = fund.get("recent_earnings_surprises", [])
    if earnings:
        rows = []
        for e in earnings:
            est = e.get("eps_estimate")
            act = e.get("eps_actual")
            surprise = e.get("surprise_pct")
            rows.append({
                "Period": e.get("period", "—"),
                "EPS Estimate": _num(est) if est is not None else "N/A",
                "EPS Actual": _num(act) if act is not None else "N/A",
                "Surprise": _pct(surprise / 100 if isinstance(surprise, (int, float)) else None)
                            if surprise is not None else "N/A",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.caption("No recent quarterly earnings data available for this ticker.")

    st.markdown("**Key Fundamental Metrics**")
    cols = st.columns(4)
    metrics = [
        ("P/E Ratio", _num(fund.get("pe_ratio"))),
        ("Forward P/E", _num(fund.get("forward_pe"))),
        ("PEG Ratio", _num(fund.get("peg_ratio"))),
        ("Price/Book", _num(fund.get("price_to_book"))),
        ("Price/Sales", _num(fund.get("price_to_sales"))),
        ("Revenue Growth (YoY)", _pct(fund.get("revenue_growth_yoy"))),
        ("Earnings Growth (YoY)", _pct(fund.get("earnings_growth_yoy"))),
        ("Gross Margin", _pct(fund.get("gross_margin"))),
        ("Operating Margin", _pct(fund.get("operating_margin"))),
        ("Profit Margin", _pct(fund.get("profit_margin"))),
        ("Return on Equity", _pct(fund.get("return_on_equity"))),
        ("Return on Assets", _pct(fund.get("return_on_assets"))),
        ("Debt/Equity", _num(fund.get("debt_to_equity"))),
        ("Current Ratio", _num(fund.get("current_ratio"))),
        ("Free Cash Flow", f"{currency}{fund['free_cash_flow_b']}B" if fund.get("free_cash_flow_b") else "N/A"),
        ("Dividend Yield", _pct(fund.get("dividend_yield"))),
        ("Beta", _num(fund.get("beta"))),
        ("Analyst Target", f"{currency}{_num(fund.get('analyst_target_price'))}" if fund.get("analyst_target_price") else "N/A"),
    ]
    for i, (label, value) in enumerate(metrics):
        cols[i % 4].metric(label, value)

    if fund.get("business_summary"):
        with st.expander("Business summary"):
            st.caption(fund["business_summary"])


def render_single_stock_result(result):
    rec_data = result["synthesis"]["recommendation"]
    ticker = result["ticker"]
    company = result.get("company_name", ticker)
    rec = rec_data.get("recommendation", "HOLD")
    confidence = rec_data.get("confidence", 0)
    score = rec_data.get("composite_score", 5)
    curr = currency_symbol(get_price_history(ticker, days=PRICE_HISTORY_DAYS).get("currency", "USD"))

    # ── Cache status badge ──────────────────────────────────────────
    was_cached = st.session_state.get("last_was_cached")
    cached_at = st.session_state.get("last_cached_at")
    if was_cached is not None and cached_at is not None:
        time_str = cached_at.strftime("%I:%M %p ET").lstrip("0")
        if was_cached:
            st.caption(f"📋 Using today's cached analysis, generated at {time_str}. "
                       "Check \"Force fresh analysis\" in the sidebar for a brand-new run.")
        else:
            st.caption(f"✨ Fresh analysis just completed at {time_str}. "
                       "Cached for the rest of today (resets 6 AM ET).")

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
        st.subheader("Price History")
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
                if key == "fundamentals":
                    st.divider()
                    render_quarterly_and_fundamentals(ticker, currency=curr)

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


# NOTE: caption used to end with "· Powered by Claude API" — removed from
# the visible UI per request; the model provider is still fully documented
# in config.py and the study guide, just not surfaced to end users.
st.title("📈 TradeDesk")
st.caption("Multi-Subagent Equity Research & Portfolio Intelligence")

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
    bucket = get_user_bucket(
        st.session_state,
        capacity=RATE_LIMIT_BUCKET_CAPACITY,
        refill_seconds_per_token=RATE_LIMIT_REFILL_SECONDS_PER_TOKEN,
    )
    if not bucket.try_consume():
        wait_minutes = max(1, int(bucket.seconds_until_next_token() / 60) + 1)
        st.error(
            f"You've hit your request limit for now ({RATE_LIMIT_BUCKET_CAPACITY} analyses). "
            f"Try again in about {wait_minutes} minute(s), or check back later — "
            "this resets gradually, not all at once."
        )
        st.stop()

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
            genuinely_invalid = not precheck.get("_transient_fetch_failure")

            if precheck.get("_transient_fetch_failure"):
                # Before trusting "transient," confirm Yahoo Finance itself
                # is actually reachable right now by checking a ticker that
                # is definitely real. If AAPL succeeds, the original failure
                # wasn't a platform-wide issue — it really was this specific
                # ticker (yfinance's error for "no such ticker" can look
                # identical to a rate-limit failure, so this is the only
                # reliable way to tell them apart).
                reference_check = get_price_history("AAPL", days=5)
                if reference_check.get("closes"):
                    genuinely_invalid = True

            if genuinely_invalid:
                st.error(
                    f"**\"{ticker_input}\"** doesn't match a real, tradeable stock. "
                    "Double-check the ticker symbol, for Indian stocks remember the "
                    "`.NS` or `.BO` suffix, e.g. `RELIANCE.NS`."
                )
            else:
                st.error(
                    f"Couldn't fetch data for **\"{ticker_input}\"** right now, "
                    "this is likely a temporary issue with the market data source, "
                    "not a problem with the ticker itself. Please wait a moment and try again."
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

        def _run_real_analysis():
            td = TradeDesk()
            return td.analyze(
                ticker=ticker_input,
                portfolio=portfolio_for_analysis,
                verbose=False,
                status_callback=update_status,
            )

        try:
            with st.spinner(f"Getting analysis for {ticker_input}..."):
                result, was_cached, cached_at = analysis_cache.get_or_compute(
                    ticker_input, _run_real_analysis,
                    portfolio=portfolio_for_analysis,
                    force_refresh=force_refresh,
                )
            status_container.empty()
            progress_bar.empty()
            st.session_state.last_result = result
            st.session_state.last_mode = "single"
            st.session_state.last_was_cached = was_cached
            st.session_state.last_cached_at = cached_at
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

# ── Track Record: usage + accuracy, rendered immediately when selected ──────
if mode == "📊 Track Record":
    st.title("📊 Track Record")
    st.caption("The receipts — how much this has actually been used, "
               "and how the recommendations have held up so far.")
    st.warning(
        "⚠️ **Not investment advice.** These numbers describe past pattern-matching, "
        "not a guarantee of future results. Please consult your CA or a licensed "
        "financial adviser before making any investment decision."
    )

    usage = analysis_cache.cache_stats()
    col1, col2 = st.columns(2)
    col1.metric("Total analyses run", usage["total_analyses"])
    col2.metric("Current cache window", usage["current_window"])

    if usage["most_popular"]:
        st.markdown("**Most-analyzed tickers**")
        for ticker, count in usage["most_popular"]:
            st.markdown(f"- {ticker}: {count} time(s)")
    else:
        st.caption("No analyses recorded yet.")

    st.divider()
    st.subheader("Accuracy")
    st.caption(
        "A recommendation only gets checked once it's had at least a week to "
        "play out. BUY/SELL need at least a 2% move in the right direction to "
        "count as correct; HOLD counts as correct if price stayed within 5%. "
        "This is a simple, transparent directional check, not a full "
        "quantitative backtest."
    )

    with st.spinner("Checking recommendations against real price history..."):
        report = accuracy_tracker.compute_track_record()

    if report["total_checked"] == 0:
        st.info(
            "Nothing old enough to check yet. Recommendations need at least "
            "7 days of real market history before they can be judged."
        )
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric(
            "Directional accuracy",
            f"{report['accuracy_pct']}%" if report["accuracy_pct"] is not None else "N/A",
        )
        col2.metric("Correct calls", report["correct"])
        col3.metric("Incorrect calls", report["incorrect"])
        if report["inconclusive"]:
            st.caption(f"{report['inconclusive']} call(s) were too close to "
                       f"call either way and aren't counted in the accuracy %.")

        with st.expander(f"See all {report['total_checked']} checked recommendations"):
            for entry in report["details"]:
                icon = {"correct": "✅", "incorrect": "❌", "inconclusive": "➖"}[entry["verdict"]]
                st.markdown(
                    f"{icon} **{entry['ticker']}** — {entry['recommendation']} "
                    f"on {entry['cached_at'][:10]}: "
                    f"${entry['baseline_price']:.2f} → ${entry['current_price']:.2f} "
                    f"({entry['pct_change']:+.1f}%)"
                )

# ── Render whatever the last completed analysis was ─────────────────────────
# This runs on every rerun, including ones triggered by chart controls, so
# those interactions redraw instantly from the stored result instead of
# re-running the 5-subagent pipeline or losing the result entirely.
elif st.session_state.last_result is not None:
    if st.session_state.last_mode == "single":
        render_single_stock_result(st.session_state.last_result)
    else:
        render_portfolio_result(st.session_state.last_result)
else:
    st.markdown("## 5 specialists. Zero forced agreement.")
    st.markdown(
        "Type a ticker and watch five research agents actually disagree, "
        "news, fundamentals, technicals, macro, and risk, then see exactly "
        "how the conflict gets resolved into one final call. No black box, "
        "no single confident guess pretending there was nothing to argue about."
    )
    st.caption("Enter a ticker in the sidebar and click **Run Analysis** to get started.")
    # NOTE (not shown in UI): synthesis runs on Claude Sonnet with extended
    # thinking, subagents on Claude Haiku, served via AWS Bedrock. Data
    # sources: Yahoo Finance (price/fundamentals), SEC EDGAR (US filings),
    # Tavily (live news search). See config.py and the study guide for detail.
    col1, col2, col3 = st.columns(3)
    col1.metric("Subagents", "5", "running in parallel")
    col2.metric("Markets", "US + India", "NSE / BSE supported")
    col3.metric("Cost to try", "Free", "no signup required")
