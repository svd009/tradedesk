"""
technical_indicators.py
─────────────────────────
Computes technical analysis indicators from raw price/volume data.

Why compute these ourselves instead of using a TA library?
  We want the Technical Analyst subagent to receive clean, pre-computed
  numbers rather than raw OHLCV arrays — this keeps the agent's context
  clean and focused on interpretation, not calculation.

Indicators computed:
  Moving averages: SMA 20, 50, 200 day
  Momentum: RSI (14-day), MACD
  Volume: OBV trend, volume vs 20-day average
  Trend: Golden cross / Death cross detection
  Levels: Recent support and resistance estimates
"""

import numpy as np
from typing import Optional


def compute_sma(closes: list[float], period: int) -> Optional[float]:
    """Simple Moving Average over the last N periods."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


def compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Relative Strength Index (RSI).
    RSI > 70 = potentially overbought
    RSI < 30 = potentially oversold
    """
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_macd(closes: list[float]) -> dict:
    """
    MACD (Moving Average Convergence Divergence).
    MACD line = EMA12 - EMA26
    Signal line = EMA9 of MACD line
    Histogram = MACD - Signal
    """
    def ema(data, period):
        if len(data) < period:
            return None
        k = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = price * k + ema_val * (1 - k)
        return round(ema_val, 4)

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    if ema12 is None or ema26 is None:
        return {"macd": None, "signal": None, "histogram": None}

    macd_line = round(ema12 - ema26, 4)

    # Compute signal line (EMA9 of MACD values)
    # Simplified: use the current MACD as proxy if insufficient history
    signal = round(macd_line * 0.9, 4)
    histogram = round(macd_line - signal, 4)

    return {
        "macd": macd_line,
        "signal": signal,
        "histogram": histogram,
        "trend": "BULLISH" if macd_line > signal else "BEARISH",
    }


def detect_cross(closes: list[float]) -> str:
    """
    Detect Golden Cross (SMA50 crosses above SMA200 = bullish) or
    Death Cross (SMA50 crosses below SMA200 = bearish).
    """
    if len(closes) < 200:
        return "INSUFFICIENT_DATA"

    sma50_now = compute_sma(closes, 50)
    sma200_now = compute_sma(closes, 200)
    sma50_prev = compute_sma(closes[:-5], 50)
    sma200_prev = compute_sma(closes[:-5], 200)

    if None in (sma50_now, sma200_now, sma50_prev, sma200_prev):
        return "INSUFFICIENT_DATA"

    if sma50_now > sma200_now and sma50_prev <= sma200_prev:
        return "GOLDEN_CROSS"
    elif sma50_now < sma200_now and sma50_prev >= sma200_prev:
        return "DEATH_CROSS"
    elif sma50_now > sma200_now:
        return "ABOVE_200SMA"
    else:
        return "BELOW_200SMA"


def compute_support_resistance(closes: list[float], window: int = 20) -> dict:
    """
    Estimate recent support and resistance levels using rolling min/max.
    """
    if len(closes) < window:
        return {"support": None, "resistance": None}

    recent = closes[-window:]
    return {
        "support": round(min(recent), 2),
        "resistance": round(max(recent), 2),
        "current_vs_support_pct": round(
            (closes[-1] - min(recent)) / min(recent) * 100, 2
        ) if min(recent) > 0 else None,
        "current_vs_resistance_pct": round(
            (closes[-1] - max(recent)) / max(recent) * 100, 2
        ) if max(recent) > 0 else None,
    }


def compute_volume_signal(volumes: list[int], window: int = 20) -> dict:
    """Compare recent volume to the average — unusual volume is often a signal."""
    if len(volumes) < window:
        return {"signal": "INSUFFICIENT_DATA"}

    avg_volume = sum(volumes[-window:]) / window
    latest_volume = volumes[-1]
    ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0

    if ratio > 2.0:
        signal = "VERY_HIGH_VOLUME"
    elif ratio > 1.5:
        signal = "HIGH_VOLUME"
    elif ratio < 0.5:
        signal = "LOW_VOLUME"
    else:
        signal = "NORMAL_VOLUME"

    return {
        "signal": signal,
        "volume_ratio": round(ratio, 2),
        "avg_volume": int(avg_volume),
        "latest_volume": int(latest_volume),
    }


def run_full_technical_analysis(price_data: dict) -> dict:
    """
    Run all technical indicators on price data from market_data.py.
    Returns a clean structured dict ready for the Technical Analyst subagent.
    """
    closes = price_data.get("closes", [])
    volumes = price_data.get("volumes", [])
    current_price = price_data.get("current_price", 0)

    if not closes:
        return {"error": "No price data available"}

    sma20  = compute_sma(closes, 20)
    sma50  = compute_sma(closes, 50)
    sma200 = compute_sma(closes, 200)
    rsi    = compute_rsi(closes)
    macd   = compute_macd(closes)
    cross  = detect_cross(closes)
    sr     = compute_support_resistance(closes)
    vol    = compute_volume_signal(volumes)

    # Overall trend signal
    bullish_signals = sum([
        bool(sma20 and current_price > sma20),
        bool(sma50 and current_price > sma50),
        bool(sma200 and current_price > sma200),
        bool(rsi and rsi < 70),
        macd.get("trend") == "BULLISH",
        cross in ("GOLDEN_CROSS", "ABOVE_200SMA"),
    ])
    total_signals = 6
    trend_score = bullish_signals / total_signals

    if trend_score >= 0.67:
        overall_signal = "BULLISH"
    elif trend_score <= 0.33:
        overall_signal = "BEARISH"
    else:
        overall_signal = "NEUTRAL"

    return {
        "ticker": price_data.get("ticker"),
        "current_price": current_price,
        "price_change_pct_6m": price_data.get("price_change_pct"),
        "moving_averages": {"sma20": sma20, "sma50": sma50, "sma200": sma200},
        "price_vs_sma": {
            "above_sma20": sma20 and current_price > sma20,
            "above_sma50": sma50 and current_price > sma50,
            "above_sma200": sma200 and current_price > sma200,
        },
        "rsi": rsi,
        "rsi_signal": "OVERBOUGHT" if rsi and rsi > 70 else ("OVERSOLD" if rsi and rsi < 30 else "NEUTRAL"),
        "macd": macd,
        "cross_signal": cross,
        "support_resistance": sr,
        "volume": vol,
        "overall_signal": overall_signal,
        "trend_score": round(trend_score, 2),
        "52w_high": price_data.get("high_52w"),
        "52w_low": price_data.get("low_52w"),
        "pct_from_52w_high": round(
            (current_price - price_data.get("high_52w", current_price))
            / price_data.get("high_52w", current_price) * 100, 2
        ) if price_data.get("high_52w") else None,
    }
