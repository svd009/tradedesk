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


def compute_rsi_series(closes: list[float], period: int = 14) -> list:
    """
    RSI for every day, aligned to `closes` (None until enough history).

    Uses Wilder's smoothing, the standard definition used by charting
    platforms: the first average is a plain mean of the first `period`
    gains/losses, and every later day blends in 1/period of the new
    value. An earlier version used a plain 14-day average for every
    day, which gives noticeably different numbers from what a user
    sees on any other site for the same stock.
    """
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return round(100 - (100 / (1 + g / l)), 2)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi(avg_gain, avg_loss)
    return out


def compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Relative Strength Index (RSI), latest value.
    RSI > 70 = potentially overbought
    RSI < 30 = potentially oversold
    """
    if len(closes) < period + 1:
        return None
    return compute_rsi_series(closes, period)[-1]


def _ema_series(data: list[float], period: int) -> list:
    """Exponential moving average for every point (None until `period` points)."""
    out = [None] * len(data)
    if len(data) < period:
        return out
    k = 2 / (period + 1)
    ema_val = sum(data[:period]) / period
    out[period - 1] = ema_val
    for i in range(period, len(data)):
        ema_val = data[i] * k + ema_val * (1 - k)
        out[i] = ema_val
    return out


def compute_macd(closes: list[float]) -> dict:
    """
    MACD (Moving Average Convergence Divergence).
    MACD line = EMA12 - EMA26
    Signal line = EMA9 of the MACD line
    Histogram = MACD - Signal

    An earlier version faked the signal line as "MACD x 0.9", which made
    the BULLISH/BEARISH label depend only on whether MACD was above zero
    and never detected a real MACD/signal crossover.
    """
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(ema12, ema26) if a is not None and b is not None]
    if not macd_series:
        return {"macd": None, "signal": None, "histogram": None}

    macd_line = macd_series[-1]
    signal = _ema_series(macd_series, 9)[-1]
    if signal is None:
        # Fewer than 34 days of history: MACD exists, signal line doesn't yet.
        return {"macd": round(macd_line, 4), "signal": None, "histogram": None,
                "trend": "BULLISH" if macd_line > 0 else "BEARISH"}

    return {
        "macd": round(macd_line, 4),
        "signal": round(signal, 4),
        "histogram": round(macd_line - signal, 4),
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


def compute_sma_series(closes: list[float], period: int) -> list:
    """
    Rolling Simple Moving Average as a full series aligned to `closes`.
    Unlike compute_sma() (which returns one current value), this returns
    one value per day so it can be plotted as a real moving line on a
    chart rather than a flat horizontal marker.
    """
    out = []
    for i in range(len(closes)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(round(sum(closes[i + 1 - period:i + 1]) / period, 2))
    return out


def compute_bollinger_bands(closes: list[float], period: int = 20, num_std: float = 2.0) -> dict:
    """
    Bollinger Bands: a middle SMA with upper/lower bands at N standard
    deviations. One of the most commonly used volatility overlays —
    price hugging the upper band suggests overbought, the lower band
    suggests oversold, and a tightening band suggests a breakout setup.
    """
    middle = compute_sma_series(closes, period)
    upper, lower = [], []
    for i in range(len(closes)):
        if i + 1 < period:
            upper.append(None)
            lower.append(None)
        else:
            window = closes[i + 1 - period:i + 1]
            std = float(np.std(window))
            upper.append(round(middle[i] + num_std * std, 2))
            lower.append(round(middle[i] - num_std * std, 2))
    return {"middle": middle, "upper": upper, "lower": lower}




def _pct_change_over(closes: list[float], trading_days: int) -> Optional[float]:
    """% change over the last N trading days (126 = about 6 months)."""
    if len(closes) <= trading_days or not closes[-trading_days - 1]:
        return None
    start = closes[-trading_days - 1]
    return round((closes[-1] - start) / start * 100, 2)


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
        "price_change_pct_6m": _pct_change_over(closes, 126),
        "price_change_pct_1y": _pct_change_over(closes, 252),
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
