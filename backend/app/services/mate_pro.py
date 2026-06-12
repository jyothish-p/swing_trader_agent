"""
MATE-PRO Scoring Engine.
Implements all 3 MATE-PRO models as mechanical scoring systems:
  1. TITAN v19 — Swing insight engine (100pt selection + v19 confluence)
  2. Swing AI v14 — 100-point selection scanner + 40-point swing engine
  3. KING v16 — Combined scanner + pattern engine + smart money + backtest

Each model takes the same raw technical data from our analysis engine and
applies its own scoring formula to produce scores, verdicts, and trade plans.
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import DailyCandle, Stock
from app.services.llm_verdict import generate_llm_one_line_verdict
from app.services.market_context import build_market_context, preload_stock_profiles, preload_news_tones
from app.services.technical import (
    _calc_ema, _calc_sma, _calc_rsi, _calc_bollinger, _calc_macd,
    _calc_vwap, _calc_fibonacci_levels, _calc_gann_levels,
    _calc_volume_profile, _detect_golden_death_cross,
)
from app.config import NSE_SUFFIX

logger = logging.getLogger(__name__)


def _to_python(obj):
    """Recursively convert numpy types to native Python types."""
    import math
    if isinstance(obj, dict):
        return {k: _to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_python(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, np.ndarray):
        return _to_python(obj.tolist())
    return obj


# ─────────────────────────────────────────────────
# RAW DATA EXTRACTION
# ─────────────────────────────────────────────────

def _extract_raw_data(db: Session, symbol: str, mode: str = "full") -> dict | None:
    """
    Extract all raw technical data needed by the 3 scoring models.
    Returns a comprehensive dict of metrics, or None if insufficient data.
    """
    # Get daily candles (1 year)
    candles = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol,
        DailyCandle.date >= datetime.now().date() - timedelta(days=400),
    ).order_by(DailyCandle.date).all()

    if not candles or len(candles) < 50:
        return None

    df = pd.DataFrame([{
        "date": c.date, "open": c.open, "high": c.high,
        "low": c.low, "close": c.close, "volume": c.volume,
    } for c in candles])

    # Use numpy arrays for raw math, pandas Series for indicator functions
    closes_np = df["close"].values.astype(float)
    highs_np = df["high"].values.astype(float)
    lows_np = df["low"].values.astype(float)
    volumes_np = df["volume"].values.astype(float)
    opens_np = df["open"].values.astype(float)

    # Pandas Series for indicator functions
    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    volumes = df["volume"].astype(float)
    opens = df["open"].astype(float)

    cmp = float(closes_np[-1])
    n = len(closes_np)

    # --- EMAs (returns pandas Series, get last value) ---
    ema_20_series = _calc_ema(closes, 20)
    ema_50_series = _calc_ema(closes, 50)
    ema_100_series = _calc_ema(closes, 100)
    ema_200_series = _calc_ema(closes, 200)
    sma_20_series = _calc_sma(closes, 20)
    sma_50_series = _calc_sma(closes, 50)

    ema_20 = float(ema_20_series.iloc[-1]) if not pd.isna(ema_20_series.iloc[-1]) else None
    ema_50 = float(ema_50_series.iloc[-1]) if not pd.isna(ema_50_series.iloc[-1]) else None
    ema_100 = float(ema_100_series.iloc[-1]) if n >= 100 and not pd.isna(ema_100_series.iloc[-1]) else None
    ema_200 = float(ema_200_series.iloc[-1]) if n >= 200 and not pd.isna(ema_200_series.iloc[-1]) else None
    sma_20 = float(sma_20_series.iloc[-1]) if not pd.isna(sma_20_series.iloc[-1]) else None
    sma_50 = float(sma_50_series.iloc[-1]) if not pd.isna(sma_50_series.iloc[-1]) else None

    # --- RSI (returns pandas Series) ---
    rsi_series = _calc_rsi(closes, 14)
    rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None

    # --- MACD (returns dict with Series) ---
    macd_result = _calc_macd(closes, 12, 26, 9)
    macd_line = float(macd_result["macd"].iloc[-1]) if not pd.isna(macd_result["macd"].iloc[-1]) else None
    macd_signal = float(macd_result["signal"].iloc[-1]) if not pd.isna(macd_result["signal"].iloc[-1]) else None
    macd_hist = float(macd_result["histogram"].iloc[-1]) if not pd.isna(macd_result["histogram"].iloc[-1]) else None
    macd_crossover = macd_result.get("crossover", "none")

    # --- Bollinger Bands (returns dict with Series) ---
    bb_result = _calc_bollinger(closes, 20, 2)
    bb_upper = float(bb_result["upper"].iloc[-1]) if not pd.isna(bb_result["upper"].iloc[-1]) else None
    bb_middle = float(bb_result["middle"].iloc[-1]) if not pd.isna(bb_result["middle"].iloc[-1]) else None
    bb_lower = float(bb_result["lower"].iloc[-1]) if not pd.isna(bb_result["lower"].iloc[-1]) else None
    bb_width = round((bb_upper - bb_lower) / bb_middle * 100, 2) if bb_middle and bb_middle > 0 and bb_upper and bb_lower else 0

    # --- Volume metrics (use numpy arrays for raw math) ---
    vol_10d = float(np.mean(volumes_np[-10:])) if n >= 10 else float(np.mean(volumes_np))
    vol_20d = float(np.mean(volumes_np[-20:])) if n >= 20 else float(np.mean(volumes_np))
    today_vol = float(volumes_np[-1])
    vol_ratio = round(today_vol / vol_20d, 2) if vol_20d > 0 else 0

    # Value traded (₹ Cr)
    value_10d = float(np.mean(closes_np[-10:] * volumes_np[-10:])) / 1e7 if n >= 10 else 0

    # --- 52W metrics ---
    high_52w = float(np.max(highs_np))
    low_52w = float(np.min(lows_np))
    pct_from_52w = round((cmp / high_52w - 1) * 100, 2) if high_52w > 0 else 0

    # --- 1M metrics ---
    n_1m = min(22, n)
    high_1m = float(np.max(highs_np[-n_1m:]))
    is_1m_new_high = float(highs_np[-1]) >= high_1m * 0.99

    # --- Trend structure detection ---
    def detect_structure(prices, period=60):
        """Detect HH/HL or LH/LL structure from swing points."""
        p = min(period, len(prices))
        segment = prices[-p:]
        if len(segment) < 20:
            return "unknown", "unknown"

        # Find local highs and lows (simple peak/trough detection)
        swing_highs = []
        swing_lows = []
        window = 5
        for i in range(window, len(segment) - window):
            if segment[i] == max(segment[i-window:i+window+1]):
                swing_highs.append(segment[i])
            if segment[i] == min(segment[i-window:i+window+1]):
                swing_lows.append(segment[i])

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "range", "neutral"

        # Check last 2-3 swing points
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]

        if hh and hl:
            return "HH/HL", "bullish"
        elif lh and ll:
            return "LH/LL", "bearish"
        else:
            return "range", "neutral"

    daily_structure, daily_bias = detect_structure(closes_np, 60)

    # Weekly structure
    weekly_closes = closes_np[::5]  # approximate weekly
    weekly_structure, weekly_bias = detect_structure(weekly_closes, 24)

    # --- Phase detection ---
    def detect_phase(c_arr, v_arr, bb_width_val):
        """Detect market phase: Accumulation, Expansion, Distribution, Markdown."""
        n_p = min(40, len(c_arr))
        recent = c_arr[-n_p:]
        vol_trend = np.mean(v_arr[-10:]) / np.mean(v_arr[-n_p:]) if np.mean(v_arr[-n_p:]) > 0 else 1

        price_range_pct = (max(recent) - min(recent)) / min(recent) * 100 if min(recent) > 0 else 0
        momentum = (c_arr[-1] / c_arr[-n_p] - 1) * 100 if c_arr[-n_p] > 0 else 0

        if bb_width_val < 8 and abs(momentum) < 5:
            return "accumulation" if momentum >= 0 else "distribution"
        elif momentum > 10 and vol_trend > 1.2:
            return "expansion"
        elif momentum < -10:
            return "markdown"
        elif momentum > 0:
            return "expansion"
        else:
            return "distribution"

    phase = detect_phase(closes_np, volumes_np, bb_width)

    # --- Volatility state ---
    bb_widths = []
    for i in range(max(0, n-20), n):
        period_slice = closes_np[max(0, i-19):i+1]
        if len(period_slice) >= 20:
            std = float(np.std(period_slice))
            mean = float(np.mean(period_slice))
            if mean > 0:
                bb_widths.append(std / mean * 100)

    if len(bb_widths) >= 2:
        if bb_widths[-1] < bb_widths[0] * 0.8:
            volatility_state = "contracting"
        elif bb_widths[-1] > bb_widths[0] * 1.2:
            volatility_state = "expanding"
        else:
            volatility_state = "stable"
    else:
        volatility_state = "stable"

    # --- Pattern detection (simplified mechanical) ---
    def detect_pattern(c_arr, h_arr, l_arr, v_arr, ema_20_val, ema_50_val):
        """Detect dominant chart pattern."""
        n_p = min(40, len(c_arr))
        recent_closes = c_arr[-n_p:]
        recent_highs = h_arr[-n_p:]
        recent_lows = l_arr[-n_p:]

        cmp_val = c_arr[-1]
        high_range = max(recent_highs)
        low_range = min(recent_lows)
        range_pct = (high_range - low_range) / low_range * 100 if low_range > 0 else 0

        # Check for tight range / box
        if range_pct < 12:
            # Check if higher lows into flat resistance (VCP/ascending triangle)
            lows_first_half = min(recent_lows[:n_p//2])
            lows_second_half = min(recent_lows[n_p//2:])
            highs_first_half = max(recent_highs[:n_p//2])
            highs_second_half = max(recent_highs[n_p//2:])

            if lows_second_half > lows_first_half * 1.01 and abs(highs_second_half - highs_first_half) / highs_first_half < 0.02:
                return "ascending_triangle", 8
            elif range_pct < 8:
                return "vcp", 8
            else:
                return "box", 6

        # Check for flag/pennant (strong move then tight consolidation)
        if n_p >= 20:
            pre_move = (c_arr[-20] / c_arr[-n_p] - 1) * 100 if c_arr[-n_p] > 0 else 0
            consolidation_range = (max(recent_highs[-10:]) - min(recent_lows[-10:])) / min(recent_lows[-10:]) * 100
            if pre_move > 10 and consolidation_range < 8:
                return "bull_flag", 8

        # Check for cup & handle
        if n_p >= 30:
            mid_low = min(recent_lows[5:n_p-5])
            start_high = max(recent_highs[:5])
            end_high = max(recent_highs[-5:])
            if mid_low < start_high * 0.92 and end_high >= start_high * 0.97:
                return "cup_handle", 7

        # Check for breakout from base
        prev_resistance = max(recent_highs[:-5]) if n_p > 5 else high_range
        if cmp_val > prev_resistance and cmp_val > (ema_20_val or 0):
            return "base_breakout", 7

        # Pullback to EMA
        if ema_20_val and ema_50_val:
            if cmp_val > ema_50_val and abs(cmp_val - ema_20_val) / ema_20_val < 0.02:
                return "pullback_ema20", 6

        # Trendline break
        if daily_bias == "bullish":
            return "trendline_break", 5

        return "unclear", 2

    pattern_name, pattern_score = detect_pattern(closes_np, highs_np, lows_np, volumes_np, ema_20, ema_50)

    # --- Trigger and invalidation levels ---
    n_20 = min(20, n)
    recent_highs_20 = highs_np[-n_20:]
    recent_lows_20 = lows_np[-n_20:]

    trigger = float(np.max(recent_highs_20))  # Breakout above recent high
    invalidation = float(np.min(recent_lows_20[-10:]))  # Recent swing low

    extension_pct = round((cmp - trigger) / trigger * 100, 2) if trigger > 0 else 0
    sl_pct = round((cmp - invalidation) / cmp * 100, 2) if cmp > 0 else 0

    # --- Support / Resistance levels ---
    fib_levels = _calc_fibonacci_levels(float(np.max(highs_np[-60:])), float(np.min(lows_np[-60:])))
    gann_levels = _calc_gann_levels(cmp)
    vol_profile = _calc_volume_profile(df, 20)

    # Support levels (from volume profile HVN, fib, and swing lows)
    supports = sorted(set([
        round(invalidation, 2),
        round(fib_levels.get("0.382", cmp * 0.95), 2) if isinstance(fib_levels, dict) else round(cmp * 0.95, 2),
        round(float(np.min(lows_np[-10:])), 2),
    ]))

    resistances = sorted(set([
        round(trigger, 2),
        round(high_52w, 2),
        round(fib_levels.get("1.272", cmp * 1.12), 2) if isinstance(fib_levels, dict) else round(cmp * 1.12, 2),
    ]))

    # --- Golden/Death cross ---
    gc_result = _detect_golden_death_cross(ema_50_series, ema_200_series)
    golden_cross = gc_result.get("golden_cross", False)
    death_cross = gc_result.get("death_cross", False)

    # --- VWAP ---
    vwap = _calc_vwap(df)

    # --- Momentum ---
    momentum_1d = round((cmp / closes_np[-2] - 1) * 100, 2) if n >= 2 else 0
    momentum_1w = round((cmp / closes_np[-5] - 1) * 100, 2) if n >= 5 else 0
    momentum_1m = round((cmp / closes_np[-22] - 1) * 100, 2) if n >= 22 else 0

    # --- Base contraction (volume declining in consolidation) ---
    if n >= 20:
        vol_first_half = float(np.mean(volumes_np[-20:-10]))
        vol_second_half = float(np.mean(volumes_np[-10:]))
        base_contraction = vol_second_half < vol_first_half * 0.85
    else:
        base_contraction = False

    # --- Close quality (near high of day) ---
    day_range = float(highs_np[-1] - lows_np[-1])
    close_position = (cmp - float(lows_np[-1])) / day_range if day_range > 0 else 0.5
    upper_wick = float(highs_np[-1] - max(opens_np[-1], closes_np[-1]))
    lower_wick = float(min(opens_np[-1], closes_np[-1]) - lows_np[-1])
    body_size = float(abs(closes_np[-1] - opens_np[-1]))
    upper_wick_pct = round(upper_wick / cmp * 100, 2) if cmp > 0 else 0
    upper_wick_ratio = round(upper_wick / body_size, 2) if body_size > 0 else (999 if upper_wick > 0 else 0)
    weak_close = close_position < 0.45
    upper_wick_heavy = upper_wick_ratio >= 1.5 and upper_wick_pct >= 0.8

    # --- Candle quality (clean vs messy) ---
    # Count gap days and spiky candles in last 20 days
    gaps = 0
    spikes = 0
    for i in range(-min(20, n), 0):
        if i > -n:
            if volumes_np[i] <= 0 or highs_np[i] == lows_np[i]:
                continue
            prev_close = closes_np[i - 1]
            gap = abs(opens_np[i] - prev_close) / prev_close * 100 if prev_close > 0 else 0
            if gap > 4:
                gaps += 1
            day_range_pct = (highs_np[i] - lows_np[i]) / closes_np[i] * 100 if closes_np[i] > 0 else 0
            body = abs(closes_np[i] - opens_np[i]) / closes_np[i] * 100 if closes_np[i] > 0 else 0
            if day_range_pct > 8 and body > 0 and day_range_pct / body > 4:
                spikes += 1

    candle_cleanliness = max(0, int(round(10 - gaps * 0.75 - spikes * 1.25)))

    # --- Overhead supply assessment ---
    nearest_overhead = None
    overhead_candidates = [level for level in (trigger, high_52w) if level and level > cmp]
    if overhead_candidates:
        nearest_overhead = min(overhead_candidates)
    overhead_gap_pct = round(((nearest_overhead - cmp) / cmp) * 100, 2) if nearest_overhead else None

    if nearest_overhead is None or overhead_gap_pct is None or overhead_gap_pct > 6:
        overhead_supply = "open_air"
    elif overhead_gap_pct >= 3:
        overhead_supply = "light"
    elif overhead_gap_pct > 0:
        overhead_supply = "moderate"
    else:
        overhead_supply = "heavy"

    # --- ATR % (proxy volatility) ---
    atr_values = []
    for i in range(-min(14, n-1), 0):
        tr = max(
            highs_np[i] - lows_np[i],
            abs(highs_np[i] - closes_np[i-1]),
            abs(lows_np[i] - closes_np[i-1])
        )
        atr_values.append(tr)
    atr = float(np.mean(atr_values)) if atr_values else 0
    atr_pct = round(atr / cmp * 100, 2) if cmp > 0 else 0

    # --- Delivery proxy (volume consistency) ---
    if n >= 10:
        mean_vol_10 = float(np.mean(volumes_np[-10:])) if np.mean(volumes_np[-10:]) > 0 else 0
        vol_consistency = float(np.std(volumes_np[-10:])) / mean_vol_10 if mean_vol_10 > 0 else 1
        if vol_ratio >= 1.5 and value_10d >= 100:
            delivery_proxy = "supportive"
        elif vol_consistency < 0.65:
            delivery_proxy = "supportive"
        elif vol_consistency > 1.35 and vol_ratio < 1.2:
            delivery_proxy = "weak"
        else:
            delivery_proxy = "neutral"
    else:
        delivery_proxy = "neutral"

    # --- Turnover ---
    turnover_cr = round(float(cmp * today_vol / 1e7), 2)
    avg_turnover_cr = round(float(cmp * vol_20d / 1e7), 2)

    # --- Market cap ---
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    market_cap_cr = stock.market_cap_cr if stock else 0

    market_context = build_market_context(
        db,
        symbol,
        cmp=round(cmp, 2),
        trigger=round(trigger, 2),
        rsi=round(rsi, 2) if rsi else None,
        momentum_1m=momentum_1m,
        weekly_structure=weekly_structure,
        mode=mode,
    )

    return {
        "symbol": symbol,
        "cmp": round(cmp, 2),
        "last_close": round(cmp, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_from_52w": pct_from_52w,
        "high_1m": round(high_1m, 2),
        "is_1m_new_high": is_1m_new_high,
        "market_cap_cr": market_cap_cr,

        # EMAs
        "ema_20": round(ema_20, 2) if ema_20 else None,
        "ema_50": round(ema_50, 2) if ema_50 else None,
        "ema_100": round(ema_100, 2) if ema_100 else None,
        "ema_200": round(ema_200, 2) if ema_200 else None,
        "sma_20": round(sma_20, 2) if sma_20 else None,
        "sma_50": round(sma_50, 2) if sma_50 else None,

        # Indicators
        "rsi": round(rsi, 2) if rsi else None,
        "macd_line": round(macd_line, 2) if macd_line else None,
        "macd_signal": round(macd_signal, 2) if macd_signal else None,
        "macd_histogram": round(macd_hist, 2) if macd_hist else None,
        "macd_crossover": (macd_hist or 0) > 0 and (macd_line or 0) > (macd_signal or 0),
        "bb_upper": round(bb_upper, 2) if bb_upper else None,
        "bb_middle": round(bb_middle, 2) if bb_middle else None,
        "bb_lower": round(bb_lower, 2) if bb_lower else None,
        "bb_width": bb_width,
        "golden_cross": golden_cross,
        "death_cross": death_cross,
        "vwap": round(vwap, 2) if vwap else None,

        # Structure
        "daily_structure": daily_structure,
        "daily_bias": daily_bias,
        "weekly_structure": weekly_structure,
        "weekly_bias": weekly_bias,
        "phase": phase,
        "volatility_state": volatility_state,

        # Pattern
        "pattern_name": pattern_name,
        "pattern_score": pattern_score,  # 0-10

        # Levels
        "trigger": round(trigger, 2),
        "invalidation": round(invalidation, 2),
        "extension_pct": extension_pct,
        "sl_pct": sl_pct,
        "supports": supports,
        "resistances": resistances,
        "fib_levels": fib_levels,
        "gann_levels": gann_levels,
        "volume_profile": vol_profile,

        # Volume
        "today_vol": int(today_vol),
        "vol_10d": round(vol_10d),
        "vol_20d": round(vol_20d),
        "vol_ratio": vol_ratio,
        "value_10d_cr": round(value_10d, 2),
        "base_contraction": base_contraction,
        "close_position": round(close_position, 2),
        "weak_close": weak_close,
        "upper_wick_pct": upper_wick_pct,
        "upper_wick_ratio": upper_wick_ratio,
        "upper_wick_heavy": upper_wick_heavy,
        "candle_cleanliness": candle_cleanliness,
        "delivery_proxy": delivery_proxy,
        "turnover_cr": turnover_cr,
        "avg_turnover_cr": avg_turnover_cr,

        # Overhead
        "overhead_supply": overhead_supply,
        "overhead_gap_pct": overhead_gap_pct,

        # Volatility
        "atr_pct": atr_pct,

        # Momentum
        "momentum_1d": momentum_1d,
        "momentum_1w": momentum_1w,
        "momentum_1m": momentum_1m,

        # TITAN v19 live context
        "market_context": market_context,
        "sector": market_context["sector"],
        "industry": market_context["industry"],
        "sector_index": market_context["sector_index"],
        "sector_weekly_rsi": market_context["sector_weekly_rsi"],
        "sector_structure": market_context["sector_structure"],
        "sector_trend_state": market_context["sector_trend_state"],
        "sector_mood": market_context["sector_mood"],
        "sector_peers": market_context["sector_peers"],
        "sector_peer_breakouts": market_context["sector_peer_breakouts"],
        "sector_positive_peers": market_context["sector_positive_peers"],
        "sector_peer_avg_perf_1m": market_context["sector_peer_avg_perf_1m"],
        "sector_perf_1m": market_context["sector_perf_1m"],
        "sector_perf_3m": market_context["sector_perf_3m"],
        "sector_perf_6m": market_context["sector_perf_6m"],
        "sector_momentum_score": market_context["sector_momentum_score"],
        "news_tone": market_context["news_tone"],
        "news_titles": market_context["news_titles"],
        "nifty_weekly_rsi": market_context["nifty_weekly_rsi"],
        "nifty_structure": market_context["nifty_structure"],
        "nifty_trend_state": market_context["nifty_trend_state"],
        "market_mood": market_context["market_mood"],
        "retail_psych": market_context["retail_psych"],
        "sentiment_score": market_context["sentiment_score"],
        "sentiment_components": market_context["sentiment_components"],
        "delivery_10d_avg_pct": market_context["delivery_10d_avg_pct"],
        "delivery_trend": market_context["delivery_trend"],
        "corporate_action_note": market_context["corporate_action_note"],
        "missing_data": market_context["missing_data"],
        "candles_df": df[["date", "open", "high", "low", "close", "volume"]].copy(),
    }


MODEL_WEIGHTS = {
    "TITAN": 0.60,
    "TITAN_v19": 0.10,
    "Swing_AI": 0.10,
    "Swing_AI_Hyper": 0.10,
    "KING": 0.10,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _verdict_from_probability(probability: float) -> str:
    if probability >= 85:
        return "STRONG BUY"
    if probability >= 75:
        return "BUY"
    if probability >= 65:
        return "HOLD"
    if probability >= 50:
        return "WAIT"
    return "AVOID"


def _score_selection_scanner(data: dict) -> dict:
    cmp = data["cmp"]
    rsi = data["rsi"] or 50
    extension_pct = data["extension_pct"]
    sl_pct = data["sl_pct"]
    pattern_name = data["pattern_name"]

    w1 = 0
    if data["weekly_structure"] == "HH/HL":
        w1 += 8
    elif data["weekly_structure"] == "range":
        w1 += 4
    if data["ema_20"] and data["ema_50"] and cmp > data["ema_20"] and cmp > data["ema_50"]:
        w1 += 6
    elif data["ema_20"] and cmp > data["ema_20"]:
        w1 += 3
    if rsi >= 60:
        w1 += 6
    elif rsi >= 55:
        w1 += 4
    elif rsi >= 50:
        w1 += 2
    w1 = min(20, w1)

    w2 = 0
    if pattern_name in ("ascending_triangle", "cup_handle", "bull_flag", "vcp", "box"):
        w2 += 16
    elif pattern_name in ("trendline_break", "rounding_bottom", "base_breakout", "pullback_ema20"):
        w2 += 12
    elif pattern_name == "unclear":
        w2 += 0
    else:
        w2 += 6
    if data["candle_cleanliness"] >= 8:
        w2 += 4
    elif data["candle_cleanliness"] >= 5:
        w2 += 2
    w2 = min(20, w2)

    w3 = 0
    if data["trigger"]:
        w3 += 4
    if data["invalidation"]:
        w3 += 4
    if abs(extension_pct) <= 2:
        w3 += 2
    elif abs(extension_pct) <= 3:
        w3 += 1
    if extension_pct > 3:
        w3 = min(w3, 4)
    w3 = min(10, w3)

    w4 = 0
    if data["vol_ratio"] >= 1.5:
        w4 += 8
    elif data["vol_ratio"] >= 1.2:
        w4 += 5
    if data["close_position"] >= 0.75:
        w4 += 4
    elif data["close_position"] >= 0.5:
        w4 += 2
    if data["base_contraction"]:
        w4 += 3
    w4 = min(15, w4)

    atr_pct = data.get("atr_pct", 0) or 0
    if 1.8 <= atr_pct <= 5.5:
        w5 = 10
    elif 1.3 <= atr_pct < 1.8 or 5.5 < atr_pct <= 7:
        w5 = 7
    elif 0.9 <= atr_pct < 1.3:
        w5 = 4
    else:
        w5 = 1

    if data["overhead_supply"] == "open_air":
        w6 = 10
    elif data["overhead_supply"] == "light":
        w6 = 6
    elif data["overhead_supply"] == "moderate":
        w6 = 2
    else:
        w6 = 0

    w7 = int(_clamp(data.get("sector_momentum_score", 0) or 0, 0, 10))

    if sl_pct <= 3:
        w8 = 5
    elif sl_pct <= 4:
        w8 = 4
    elif sl_pct <= 5:
        w8 = 3
    else:
        w8 = 1

    selection_total = w1 + w2 + w3 + w4 + w5 + w6 + w7 + w8
    if selection_total >= 85:
        selection_grade = "A+"
    elif selection_total >= 70:
        selection_grade = "A"
    elif selection_total >= 55:
        selection_grade = "B"
    else:
        selection_grade = "SKIP"

    return {
        "selection_total": selection_total,
        "selection_grade": selection_grade,
        "components": {
            "W1_weekly_tailwind": {"score": w1, "max": 20},
            "W2_daily_setup_quality": {"score": w2, "max": 20},
            "W3_trigger_clarity": {"score": w3, "max": 10},
            "W4_volume_power": {"score": w4, "max": 15},
            "W5_move_capacity": {"score": w5, "max": 10},
            "W6_overhead_supply": {"score": w6, "max": 10},
            "W7_sector_momentum": {"score": w7, "max": 10},
            "W8_risk_quality": {"score": w8, "max": 5},
        },
    }


# ─────────────────────────────────────────────────
def _score_titan_v20(data: dict) -> dict:
    """
    TITAN v20 scoring based on the v20 master scanner doc:
      - strict liquidity gate
      - 100-point selection scanner
      - raw confluence calculator with sector, sentiment, backtest and sweep inputs
    """
    cmp = data["cmp"]
    extension_pct = data["extension_pct"]
    sl_pct = data["sl_pct"]
    selection = _score_selection_scanner(data)
    selection_total = selection["selection_total"]
    selection_grade = selection["selection_grade"]

    setup_family_map = {
        "vcp": "clean_base_breakout",
        "ascending_triangle": "clean_base_breakout",
        "bull_flag": "clean_base_breakout",
        "box": "clean_base_breakout",
        "cup_handle": "clean_base_breakout",
        "base_breakout": "clean_base_breakout",
        "pullback_ema20": "pullback_continuation",
        "trendline_break": "breakout_retest",
        "rounding_bottom": "breakout_retest",
    }
    setup_family = setup_family_map.get(data["pattern_name"])

    gate_checks = {
        "avg_value": (data.get("value_10d_cr", 0) or 0) >= 50,
        "avg_volume": (data.get("vol_10d", 0) or 0) > 0,
        "spread_slippage": data.get("candle_cleanliness", 0) >= 4 and (data.get("atr_pct", 0) or 0) <= 8,
        "price_range": 50 <= cmp <= 3000,
        "chart_cleanliness": data.get("candle_cleanliness", 0) >= 4,
    }
    gate_fail_reasons = []
    if not gate_checks["avg_value"]:
        gate_fail_reasons.append("10D avg traded value below ₹50 Cr")
    if not gate_checks["avg_volume"]:
        gate_fail_reasons.append("10D avg volume unavailable")
    if not gate_checks["spread_slippage"]:
        gate_fail_reasons.append("spread/slippage proxy weak")
    if not gate_checks["price_range"]:
        gate_fail_reasons.append("price outside ₹50–₹3000")
    if not gate_checks["chart_cleanliness"]:
        gate_fail_reasons.append("chart cleanliness weak")
    gate_pass = all(gate_checks.values())

    classical_score = min(10, data.get("pattern_score", 0) or 0)
    harmonic_score = 4 if data.get("fib_levels") and data.get("pattern_name") in ("cup_handle", "vcp", "bull_flag") else 0
    gann_score = 5 if data.get("gann_levels") else 0
    pattern_engine = round(((classical_score + harmonic_score + gann_score) / 25) * 100, 1)

    liquidity_score = 0
    if data["phase"] == "accumulation":
        liquidity_score += 4
    elif data["phase"] == "expansion":
        liquidity_score += 3
    if data["delivery_proxy"] == "supportive":
        liquidity_score += 2
    elif data["delivery_proxy"] == "neutral":
        liquidity_score += 1
    if data["vwap"] and cmp > data["vwap"]:
        liquidity_score += 2
    if data["overhead_supply"] in ("open_air", "light"):
        liquidity_score += 2
    liquidity_score = min(10, liquidity_score)

    rsi = data["rsi"] or 50
    indicator_score = 0
    if 55 <= rsi <= 68:
        indicator_score += 5
    elif 50 <= rsi <= 75:
        indicator_score += 3
    if data["macd_crossover"]:
        indicator_score += 5
    elif data["macd_histogram"] and data["macd_histogram"] > 0:
        indicator_score += 3
    if data["ema_20"] and data["ema_50"] and data["ema_200"] and data["ema_20"] > data["ema_50"] > data["ema_200"]:
        indicator_score += 5
    elif data["ema_20"] and data["ema_50"] and data["ema_20"] > data["ema_50"]:
        indicator_score += 3
    if data["bb_width"] < 8:
        indicator_score += 5
    elif data["volatility_state"] == "contracting":
        indicator_score += 3
    indicator_score = min(20, indicator_score)

    fib_avwap_score = 0
    if data["fib_levels"]:
        fib_avwap_score += 4
    if data["vwap"] and cmp > data["vwap"]:
        fib_avwap_score += 3
    if data["supports"]:
        fib_avwap_score += 3
    fib_avwap_score = min(10, fib_avwap_score)

    selection_components = selection["components"]
    weekly20 = selection_components["W1_weekly_tailwind"]["score"]
    daily20 = selection_components["W2_daily_setup_quality"]["score"]
    trigger10 = selection_components["W3_trigger_clarity"]["score"]
    smart_liquidity = liquidity_score
    indicator20 = indicator_score
    overhead10 = selection_components["W6_overhead_supply"]["score"]
    multi_tf = 0
    if data["daily_bias"] == "bullish":
        multi_tf += 2
    if data["weekly_bias"] == "bullish":
        multi_tf += 3
    risk5 = selection_components["W8_risk_quality"]["score"]
    base_swing_score = 0
    base_swing_score += 7 if weekly20 >= 16 else 5 if weekly20 >= 12 else 3 if weekly20 >= 8 else 1
    base_swing_score += 7 if daily20 >= 16 and trigger10 >= 8 else 5 if daily20 >= 14 and trigger10 >= 7 else 3 if daily20 >= 10 else 1
    base_swing_score += 6 if smart_liquidity >= 8 else 4 if smart_liquidity >= 5 else 2 if smart_liquidity >= 3 else 1
    base_swing_score += 4 if overhead10 >= 8 else 3 if overhead10 >= 5 else 1
    base_swing_score += 5 if indicator20 >= 15 else 3 if indicator20 >= 10 else 1
    base_swing_score += 5 if multi_tf >= 4 else 3 if multi_tf >= 2 else 1
    base_swing_score += 6 if risk5 >= 5 else 4 if risk5 >= 4 else 2 if risk5 >= 3 else 1
    base_swing_score = min(40, base_swing_score)

    velocity_points = 1
    if data["volatility_state"] == "expanding" and data["vol_ratio"] >= 1.5:
        velocity_points = 5
    elif data["volatility_state"] == "contracting" and data["vol_ratio"] >= 1.2:
        velocity_points = 4
    elif data["momentum_1d"] > 2 or data["momentum_1w"] > 4:
        velocity_points = 3
    elif data["momentum_1d"] > 0:
        velocity_points = 2

    sector_strength_score = round(_clamp((data.get("sector_momentum_score", 0) or 0) * 10, 0, 100), 1)
    sentiment_score = round(_clamp(data.get("sentiment_score", 0) or 0, 0, 100), 1)

    backtest_score = 0
    candles = data.get("candles_df")
    samples = 0
    win_rate = 0
    t1_rate = 0
    t2_rate = 0
    false_rate = 0
    if candles is not None and len(candles) >= 120:
        closes = candles["close"].astype(float).reset_index(drop=True)
        highs = candles["high"].astype(float).reset_index(drop=True)
        volumes = candles["volume"].astype(float).reset_index(drop=True)
        wins = 0
        t1_hits = 0
        t2_hits = 0
        false_breakouts = 0
        for idx in range(40, len(closes) - 20):
            breakout_level = highs.iloc[idx - 20:idx].max()
            volume_avg = volumes.iloc[max(0, idx - 20):idx].mean() or 0
            if closes.iloc[idx] > breakout_level and volumes.iloc[idx] >= volume_avg * 1.2:
                samples += 1
                entry = closes.iloc[idx]
                window = closes.iloc[idx + 1:idx + 21]
                max_gain = ((window.max() / entry) - 1) * 100 if len(window) else 0
                min_gain = ((window.min() / entry) - 1) * 100 if len(window) else 0
                if max_gain >= 10:
                    wins += 1
                    t1_hits += 1
                if max_gain >= 15:
                    t2_hits += 1
                if min_gain <= -5 and max_gain < 8:
                    false_breakouts += 1
        if samples:
            win_rate = wins / samples
            t1_rate = t1_hits / samples
            t2_rate = t2_hits / samples
            false_rate = false_breakouts / samples
            backtest_score = 20 * (
                (win_rate * 0.45) +
                (t1_rate * 0.2) +
                (t2_rate * 0.2) +
                ((1 - false_rate) * 0.15)
            )
    backtest_score = round(_clamp(backtest_score, 0, 20), 1)

    sweep_risk = 3
    if data["phase"] == "accumulation" and data["vol_ratio"] >= 1.2:
        sweep_risk = 2
    elif data["phase"] == "distribution":
        sweep_risk = 7
    elif data["overhead_supply"] == "heavy":
        sweep_risk = 8
    elif data["delivery_proxy"] == "weak":
        sweep_risk = 6
    sweep_penalty = sweep_risk * 1.5

    sector_boost_points = 6 if sector_strength_score >= 75 else 4 if sector_strength_score >= 60 else 2 if sector_strength_score >= 50 else 0 if sector_strength_score >= 35 else -2
    sentiment_adjustment = 4 if sentiment_score >= 75 else 2 if sentiment_score >= 60 else -2 if sentiment_score < 35 else 0
    backtest_adjustment = 5 if backtest_score >= 16 else 3 if backtest_score >= 12 else -3 if backtest_score < 7 else 0

    raw_confluence = (
        selection_total * 0.22 +
        (base_swing_score * 2.5) * 0.27 +
        pattern_engine * 0.09 +
        (liquidity_score * 10) * 0.09 +
        (indicator_score * 5) * 0.09 +
        (fib_avwap_score * 10) * 0.04 +
        (velocity_points * 20) * 0.05 +
        sector_strength_score * 0.03 +
        sentiment_score * 0.02 +
        (backtest_score * 5) * 0.10 -
        sweep_penalty
    )
    final_probability = round(_clamp(raw_confluence + sector_boost_points + sentiment_adjustment + backtest_adjustment, 0, 100), 1)

    if not gate_pass or not setup_family:
        verdict = "AVOID"
    else:
        verdict = _verdict_from_probability(final_probability)
        if extension_pct > 5:
            verdict = "AVOID"
        elif extension_pct > 3:
            verdict = "WAIT"
        elif sl_pct > 5:
            verdict = "WAIT" if final_probability >= 65 else "AVOID"
        elif samples and win_rate < 0.35:
            verdict = "WAIT" if verdict in ("BUY", "STRONG BUY") else verdict
        elif false_rate > 0.50:
            verdict = "WAIT" if verdict in ("BUY", "STRONG BUY") else verdict
        elif samples < 5 and verdict == "STRONG BUY":
            verdict = "BUY"

    if not gate_pass or not setup_family:
        selection_action = "SKIP"
    elif extension_pct > 5:
        selection_action = "SKIP"
    elif extension_pct > 3:
        selection_action = "WAIT RETEST"
    elif sl_pct > 5:
        selection_action = "ALERT ONLY"
    elif verdict in ("BUY", "STRONG BUY"):
        selection_action = "TRADE"
    elif verdict == "HOLD":
        selection_action = "WAIT RETEST"
    elif verdict == "WAIT":
        selection_action = "ALERT ONLY"
    else:
        selection_action = "SKIP"

    positional_score = min(30, round(
        (selection_total / 100) * 10 +
        (base_swing_score / 40) * 8 +
        (backtest_score / 20) * 6 +
        (sector_strength_score / 100) * 3 +
        (sentiment_score / 100) * 3
    ))

    return {
        "model": "TITAN v20",
        "scanner_score": round(selection_total, 1),
        "scanner_raw": round(raw_confluence, 1),
        "penalties": round(max(0, selection_total - final_probability), 1),
        "penalty_reasons": gate_fail_reasons,
        "components": selection["components"],
        "positional_score": positional_score,
        "positional_max": 30,
        "positional_eligible": gate_pass and setup_family is not None and sl_pct <= 5,
        "probability_pct": final_probability,
        "verdict": verdict,
        "liquidity_gate": "PASS" if gate_pass else "FAIL",
        "gate_checks": gate_checks,
        "selection_grade": selection_grade,
        "selection_action": selection_action,
        "setup_family": setup_family or "invalid",
        "pattern_engine": round(pattern_engine, 1),
        "liquidity_score": liquidity_score,
        "indicator_score": indicator_score,
        "fib_avwap_score": fib_avwap_score,
        "base_weekly_score": base_swing_score,
        "velocity_points": velocity_points,
        "sector_strength_score": sector_strength_score,
        "sentiment_score": sentiment_score,
        "backtest_score": backtest_score,
        "backtest": {
            "sample_size": samples,
            "win_rate_percent": round(win_rate * 100, 1),
            "t1_hit_rate_percent": round(t1_rate * 100, 1),
            "t2_hit_rate_percent": round(t2_rate * 100, 1),
            "false_breakout_rate_percent": round(false_rate * 100, 1),
        },
        "sweep_risk": sweep_risk,
        "sector_context": {
            "sector": data.get("sector"),
            "industry": data.get("industry"),
            "sector_index": data.get("sector_index"),
            "sector_weekly_rsi": data.get("sector_weekly_rsi"),
            "sector_structure": data.get("sector_structure"),
            "sector_peers": data.get("sector_peers"),
            "sector_peer_breakouts": data.get("sector_peer_breakouts"),
            "sector_positive_peers": data.get("sector_positive_peers"),
            "sector_peer_avg_perf_1m": data.get("sector_peer_avg_perf_1m"),
            "sector_perf_1m": data.get("sector_perf_1m"),
            "sector_perf_3m": data.get("sector_perf_3m"),
            "sector_perf_6m": data.get("sector_perf_6m"),
            "sector_momentum_score": data.get("sector_momentum_score"),
        },
        "sentiment_filter": {
            "news_tone": data.get("news_tone"),
            "sector_mood": data.get("sector_mood"),
            "nifty_mood": data.get("market_mood"),
            "retail_psych": data.get("retail_psych"),
            "sentiment_score": data.get("sentiment_score"),
        },
        "risk_note": {
            "delivery_10d_avg_pct": data.get("delivery_10d_avg_pct"),
            "delivery_trend": data.get("delivery_trend"),
            "corporate_action_note": data.get("corporate_action_note"),
            "missing_data": data.get("missing_data", []),
        },
    }


# MODEL 1: TITAN v19 — Swing Insight (100) + Confluence (30)
# ─────────────────────────────────────────────────

def _score_titan_v19(data: dict) -> dict:
    """
    TITAN v19 scoring based on the provided swing-engine spec.
    Pass 1:
      - Liquidity gate
      - 100-point weekly swing selection scanner
    Pass 2:
      - Internal confluence layers compressed into a compatibility 30-point score
    Missing external inputs are marked as DATA NOT PROVIDED and score 0.
    """
    cmp = data["cmp"]
    rsi = data["rsi"] or 50
    sl_pct = data["sl_pct"]
    extension_pct = data["extension_pct"]
    market_context = data.get("market_context", {})
    retail_psych = data.get("retail_psych") or market_context.get("retail_psych") or "Neutral"

    valid_families = {
        "vcp": "clean_base_breakout",
        "ascending_triangle": "clean_base_breakout",
        "bull_flag": "clean_base_breakout",
        "box": "clean_base_breakout",
        "cup_handle": "clean_base_breakout",
        "base_breakout": "clean_base_breakout",
        "pullback_ema20": "pullback_continuation",
    }
    setup_family = valid_families.get(data["pattern_name"])

    gate_checks = {
        "avg_value": data.get("value_10d_cr", 0) >= 50,
        "volume_stable": data.get("delivery_proxy") != "weak" and data.get("vol_10d", 0) > 0,
        "chart_clean": data.get("candle_cleanliness", 0) >= 4,
        "levels_visible": bool(data.get("trigger") and data.get("invalidation")),
    }
    gate_fail_reasons = []
    if not gate_checks["avg_value"]:
        gate_fail_reasons.append("10D avg value traded < ₹50 Cr")
    if not gate_checks["volume_stable"]:
        gate_fail_reasons.append("volume stability weak")
    if not gate_checks["chart_clean"]:
        gate_fail_reasons.append("chart clarity weak")
    if not gate_checks["levels_visible"]:
        gate_fail_reasons.append("trigger/invalidation not visible")
    gate_pass = all(gate_checks.values())

    # 2.1 Weekly Trend Tailwind (0-20)
    w1 = 0
    if data["weekly_structure"] == "HH/HL":
        w1 += 8
    elif data["weekly_structure"] == "range":
        w1 += 4
    if data["ema_50"] and data["ema_200"] and cmp > data["ema_50"] and cmp > data["ema_200"]:
        w1 += 6
    elif data["ema_50"] and cmp > data["ema_50"]:
        w1 += 3
    if rsi >= 60:
        w1 += 6
    elif rsi >= 55:
        w1 += 4
    elif rsi >= 50:
        w1 += 2
    w1 = min(20, w1)

    # 2.2 Daily Setup Quality (0-20)
    w2 = 0
    if data["pattern_name"] in ("vcp", "cup_handle", "bull_flag", "ascending_triangle", "box"):
        w2 += 16
    elif data["pattern_name"] in ("base_breakout", "pullback_ema20"):
        w2 += 12
    elif data["pattern_name"] == "unclear":
        w2 += 0
    else:
        w2 += 6
    if data["candle_cleanliness"] >= 8:
        w2 += 4
    elif data["candle_cleanliness"] >= 6:
        w2 += 3
    elif data["candle_cleanliness"] >= 4:
        w2 += 2
    w2 = min(20, w2)

    # 2.3 Trigger Clarity (0-10)
    w3 = 0
    if data["trigger"] and data["invalidation"]:
        w3 += 6
    if abs(extension_pct) <= 1.5:
        w3 += 4
    elif abs(extension_pct) <= 3:
        w3 += 2
    w3 = min(10, w3)

    # 2.4 Volume Power (0-15)
    w4 = 0
    if data["vol_ratio"] >= 1.5:
        w4 += 8
    elif data["vol_ratio"] >= 1.2:
        w4 += 5
    if data["close_position"] >= 0.75:
        w4 += 4
    elif data["close_position"] >= 0.5:
        w4 += 2
    if data["base_contraction"]:
        w4 += 3
    w4 = min(15, w4)

    # 2.5 Volatility / Move Capacity (0-10)
    w5 = 0
    atr_pct = data.get("atr_pct", 0)
    if 1.8 <= atr_pct <= 5.5:
        w5 = 10
    elif 1.3 <= atr_pct < 1.8 or 5.5 < atr_pct <= 7:
        w5 = 7
    elif 0.9 <= atr_pct < 1.3:
        w5 = 4
    else:
        w5 = 1

    # 2.6 Overhead Supply / Resistance (0-10)
    w6 = 0
    if data["overhead_supply"] == "open_air":
        w6 = 10
    elif data["overhead_supply"] == "light":
        w6 = 6
    elif data["overhead_supply"] == "moderate":
        w6 = 2
    else:
        w6 = 0

    # 2.7 Sector Momentum (0-10)
    w7 = int(max(0, min(10, data.get("sector_momentum_score", 0) or 0)))

    # 2.8 Risk Quality (0-5)
    if sl_pct <= 3:
        w8 = 5
    elif sl_pct <= 4:
        w8 = 4
    elif sl_pct <= 5:
        w8 = 3
    else:
        w8 = 1

    selection_total = w1 + w2 + w3 + w4 + w5 + w6 + w7 + w8

    if selection_total >= 85:
        selection_grade = "A+"
    elif selection_total >= 70:
        selection_grade = "A"
    elif selection_total >= 55:
        selection_grade = "B"
    else:
        selection_grade = "SKIP"

    # Pass-2 internal engines compressed from available data only.
    pattern_engine = min(100, int((data["pattern_score"] / 10) * 100))
    liquidity_score = 0
    if data["phase"] == "accumulation":
        liquidity_score += 4
    elif data["phase"] == "expansion":
        liquidity_score += 3
    if data["delivery_proxy"] == "supportive":
        liquidity_score += 3
    elif data["delivery_proxy"] == "neutral":
        liquidity_score += 1
    if data["vwap"] and cmp > data["vwap"]:
        liquidity_score += 3
    liquidity_score = min(10, liquidity_score)

    indicator_score = 0
    if rsi and 55 <= rsi <= 68:
        indicator_score += 5
    elif rsi and 50 <= rsi <= 75:
        indicator_score += 3
    if data["macd_crossover"]:
        indicator_score += 5
    elif data["macd_histogram"] and data["macd_histogram"] > 0:
        indicator_score += 3
    if (data["ema_20"] and data["ema_50"] and data["ema_200"]
            and data["ema_20"] > data["ema_50"] > data["ema_200"]):
        indicator_score += 5
    elif data["ema_20"] and data["ema_50"] and data["ema_20"] > data["ema_50"]:
        indicator_score += 3
    if data["volatility_state"] == "contracting" or data["bb_width"] < 8:
        indicator_score += 5
    indicator_score = min(20, indicator_score)

    fib_avwap_score = 0
    if data["vwap"] and cmp > data["vwap"]:
        fib_avwap_score += 5
    if data["fib_levels"]:
        fib_avwap_score += 5
    fib_avwap_score = min(10, fib_avwap_score)

    base_weekly_score = 0
    base_weekly_score += min(7, round(w1 / 3))
    base_weekly_score += min(7, round((w2 + w3) / 5))
    base_weekly_score += min(6, liquidity_score)
    base_weekly_score += 4 if data["overhead_supply"] in ("open_air", "light") else 2 if data["overhead_supply"] == "moderate" else 0
    base_weekly_score += min(5, round(indicator_score / 4))
    mtf_confluence = 0
    if data["daily_bias"] == "bullish":
        mtf_confluence += 2
    if data["weekly_bias"] == "bullish":
        mtf_confluence += 3
    base_weekly_score += min(5, mtf_confluence)
    base_weekly_score += min(6, w8 + 1)
    sentiment_score = data.get("sentiment_score", 0) or 0
    if sentiment_score >= 75:
        base_weekly_score += 2
    elif sentiment_score >= 55:
        base_weekly_score += 1
    elif sentiment_score <= 25:
        base_weekly_score -= 1
    base_weekly_score = min(40, base_weekly_score)

    velocity_points = 0
    if data["volatility_state"] == "contracting" and data["vol_ratio"] >= 1.2:
        velocity_points = 5
    elif data["volatility_state"] in ("contracting", "expanding"):
        velocity_points = 3
    elif data["vol_ratio"] >= 1.2:
        velocity_points = 2

    sweep_risk = 0
    if data["phase"] == "accumulation":
        sweep_risk += 4
    elif data["phase"] == "expansion":
        sweep_risk += 2
    if data["pattern_name"] in ("vcp", "ascending_triangle"):
        sweep_risk += 3
    if data["delivery_proxy"] == "supportive":
        sweep_risk += 3
    sweep_risk = min(10, sweep_risk)

    positional_score = min(30, round(
        (pattern_engine / 100) * 7 +
        liquidity_score * 0.5 +
        (indicator_score / 20) * 6 +
        (fib_avwap_score / 10) * 4 +
        (base_weekly_score / 40) * 8 +
        velocity_points * 0.5
    ))

    # Probability map
    if selection_total >= 85:
        probability = 88
    elif selection_total >= 70:
        probability = 76
    elif selection_total >= 55:
        probability = 61
    else:
        probability = 42
    probability = max(0, min(100, probability + (2 if sentiment_score >= 75 else 0) - (4 if sentiment_score <= 25 else 0)))

    # Verdict
    if not gate_pass:
        verdict = "AVOID"
    elif not setup_family:
        verdict = "AVOID"
    elif extension_pct > 5:
        verdict = "AVOID"
    elif sl_pct > 5:
        verdict = "WAIT" if selection_total >= 65 else "AVOID"
    elif extension_pct > 3:
        verdict = "WAIT"
    elif sentiment_score <= 25 and selection_total >= 70:
        verdict = "WAIT"
    elif selection_total < 70:
        verdict = "WAIT"
    elif selection_total >= 85 and base_weekly_score >= 28:
        verdict = "STRONG BUY"
    elif selection_total >= 70:
        verdict = "BUY"
    else:
        verdict = "HOLD"

    if not gate_pass:
        selection_action = "SKIP"
    elif not setup_family:
        selection_action = "SKIP"
    elif extension_pct > 5:
        selection_action = "SKIP"
    elif extension_pct > 3:
        selection_action = "WAIT RETEST"
    elif retail_psych == "FOMO" and extension_pct > 3:
        selection_action = "WAIT RETEST"
    elif selection_total >= 70:
        selection_action = "TRADE"
    elif selection_total >= 55:
        selection_action = "ALERT ONLY"
    else:
        selection_action = "SKIP"

    positional_eligible = (
        gate_pass
        and setup_family is not None
        and selection_total >= 70
        and sl_pct <= 5
        and data["overhead_supply"] != "heavy"
    )

    return {
        "model": "TITAN v19",
        "scanner_score": selection_total,
        "scanner_raw": selection_total,
        "penalties": 0,
        "penalty_reasons": gate_fail_reasons,
        "components": {
            "W1_weekly_tailwind": {"score": w1, "max": 20},
            "W2_daily_setup_quality": {"score": w2, "max": 20},
            "W3_trigger_clarity": {"score": w3, "max": 10},
            "W4_volume_power": {"score": w4, "max": 15},
            "W5_move_capacity": {"score": w5, "max": 10},
            "W6_overhead_supply": {"score": w6, "max": 10},
            "W7_sector_momentum": {"score": w7, "max": 10},
            "W8_risk_quality": {"score": w8, "max": 5},
        },
        "positional_score": positional_score,
        "positional_max": 30,
        "positional_eligible": positional_eligible,
        "probability_pct": probability,
        "verdict": verdict,
        "liquidity_gate": "PASS" if gate_pass else "FAIL",
        "gate_checks": gate_checks,
        "selection_grade": selection_grade,
        "selection_action": selection_action,
        "setup_family": setup_family or "invalid",
        "pattern_engine": pattern_engine,
        "liquidity_score": liquidity_score,
        "indicator_score": indicator_score,
        "fib_avwap_score": fib_avwap_score,
        "base_weekly_score": base_weekly_score,
        "velocity_points": velocity_points,
        "sweep_risk": sweep_risk,
        "sector_context": {
            "sector": data.get("sector"),
            "industry": data.get("industry"),
            "sector_index": data.get("sector_index"),
        "sector_weekly_rsi": data.get("sector_weekly_rsi"),
        "sector_structure": data.get("sector_structure"),
        "sector_peers": data.get("sector_peers"),
        "sector_peer_breakouts": data.get("sector_peer_breakouts"),
        "sector_positive_peers": data.get("sector_positive_peers"),
        "sector_peer_avg_perf_1m": data.get("sector_peer_avg_perf_1m"),
        "sector_perf_1m": data.get("sector_perf_1m"),
        "sector_perf_3m": data.get("sector_perf_3m"),
        "sector_perf_6m": data.get("sector_perf_6m"),
        "sector_momentum_score": data.get("sector_momentum_score"),
        },
        "sentiment_filter": {
            "news_tone": data.get("news_tone"),
            "sector_mood": data.get("sector_mood"),
            "nifty_mood": data.get("market_mood"),
            "retail_psych": data.get("retail_psych"),
            "sentiment_score": data.get("sentiment_score"),
        },
        "risk_note": {
            "delivery_10d_avg_pct": data.get("delivery_10d_avg_pct"),
            "delivery_trend": data.get("delivery_trend"),
            "corporate_action_note": data.get("corporate_action_note"),
            "missing_data": data.get("missing_data", []),
        },
    }


# ─────────────────────────────────────────────────
# MODEL 2: SWING AI v14 — Selection (100) + Swing Engine (40)
# ─────────────────────────────────────────────────

def _score_swing_ai_legacy(data: dict) -> dict:
    """
    Legacy Swing AI v14 engine retained only for reference and no longer used in the active composite.
    Focus on velocity, sweep models, and sentiment.
    """
    cmp = data["cmp"]
    rsi = data["rsi"] or 50

    # 2.1 Weekly Trend Tailwind (0-20)
    w1 = 0
    if data["weekly_structure"] == "HH/HL":
        w1 += 8
    elif data["weekly_structure"] == "range":
        w1 += 4
    # Position vs EMAs (weekly approximation)
    if data["ema_50"] and cmp > data["ema_50"]:
        w1 += 3
    if data["ema_200"] and cmp > data["ema_200"]:
        w1 += 3
    # RSI
    if rsi >= 60:
        w1 += 6
    elif rsi >= 55:
        w1 += 4
    elif rsi >= 50:
        w1 += 2
    w1 = min(20, w1)

    # 2.2 Daily Setup Quality (0-20)
    w2 = 0
    pn = data["pattern_name"]
    if pn in ("vcp", "cup_handle", "bull_flag", "ascending_triangle"):
        w2 += 16
    elif pn in ("trendline_break", "base_breakout", "pullback_ema20"):
        w2 += 12
    elif pn == "box":
        w2 += 6
    else:
        w2 += 0
    # Cleanliness bonus
    if data["candle_cleanliness"] >= 8:
        w2 += 4
    elif data["candle_cleanliness"] >= 5:
        w2 += 2
    w2 = min(20, w2)

    # 2.3 Trigger Clarity + Levels (0-10)
    w3 = 0
    if data["trigger"]:
        w3 += 4
    if data["invalidation"]:
        w3 += 4
    if abs(data["extension_pct"]) <= 2:
        w3 += 2
    if data["extension_pct"] > 3:
        w3 = min(w3, 4)
    w3 = min(10, w3)

    # 2.4 Volume Power (0-15)
    w4 = 0
    if data["vol_ratio"] >= 1.5:
        w4 += 8
    elif data["vol_ratio"] >= 1.2:
        w4 += 5
    if data["close_position"] >= 0.75:
        w4 += 4
    elif data["close_position"] >= 0.5:
        w4 += 2
    if data["base_contraction"]:
        w4 += 3
    w4 = min(15, w4)

    # 2.5 Volatility / Move Capacity (0-10)
    w5 = 0
    atr_pct = data["atr_pct"]
    if atr_pct >= 2.0:
        w5 = 10  # Supports 10-25% moves
    elif atr_pct >= 1.5:
        w5 = 8
    elif atr_pct >= 1.0:
        w5 = 6
    else:
        w5 = 2

    # 2.6 Overhead Supply / Resistance (0-10)
    w6 = 0
    if data["overhead_supply"] == "open_air":
        w6 = 10
    elif data["overhead_supply"] == "light":
        w6 = 6
    elif data["overhead_supply"] == "moderate":
        w6 = 2
    else:
        w6 = 0

    # 2.7 Sector Momentum (0-10) — approximated from momentum
    w7 = 0
    if data["momentum_1m"] > 5:
        w7 = 8
    elif data["momentum_1m"] > 2:
        w7 = 6
    elif data["momentum_1m"] > 0:
        w7 = 4
    else:
        w7 = 2

    # 2.8 Risk Quality (0-5)
    w8 = 0
    sl_pct = data["sl_pct"]
    if 0 < sl_pct <= 3:
        w8 = 5
    elif sl_pct <= 4:
        w8 = 4
    elif sl_pct <= 5:
        w8 = 3
    else:
        w8 = 1

    selection_total = w1 + w2 + w3 + w4 + w5 + w6 + w7 + w8

    # Hard overrides
    if data["extension_pct"] > 5:
        verdict_override = "SKIP"
    elif sl_pct > 5:
        verdict_override = "WAIT" if selection_total >= 70 else "SKIP"
    else:
        verdict_override = None

    # Selection grade
    if selection_total >= 85:
        selection_grade = "A+"
        selection_action = "TRADE-READY"
    elif selection_total >= 70:
        selection_grade = "A"
        selection_action = "TRADE-READY"
    elif selection_total >= 55:
        selection_grade = "B"
        selection_action = "WATCHLIST"
    else:
        selection_grade = "C"
        selection_action = "SKIP"

    # --- Swing Engine (0-40) ---
    # 9.1 Weekly trend (0-7)
    sw1 = min(7, int(w1 * 7 / 20))

    # 9.2 Daily pattern+trigger (0-7)
    sw2 = min(7, int((w2 + w3) * 7 / 30))

    # 9.3 Smart money/liquidity (0-6)
    sw3 = 0
    if data["phase"] == "accumulation":
        sw3 += 4
    elif data["phase"] == "expansion":
        sw3 += 3
    if data["delivery_proxy"] == "supportive":
        sw3 += 2
    sw3 = min(6, sw3)

    # 9.4 VRVP/LVN pocket (0-4)
    sw4 = 0
    if data["overhead_supply"] in ("open_air", "light"):
        sw4 = 4
    elif data["overhead_supply"] == "moderate":
        sw4 = 2

    # 9.5 Indicator alignment (0-5)
    sw5 = 0
    if data["macd_crossover"]:
        sw5 += 2
    if rsi and 55 <= rsi <= 68:
        sw5 += 2
    if data["ema_20"] and data["ema_50"] and data["ema_20"] > data["ema_50"]:
        sw5 += 1
    sw5 = min(5, sw5)

    # 9.6 Multi-TF confluence (0-5)
    sw6 = 0
    if data["daily_bias"] == "bullish":
        sw6 += 2
    if data["weekly_bias"] == "bullish":
        sw6 += 3
    sw6 = min(5, sw6)

    # 9.7 Risk fit (0-6)
    sw7 = 0
    if sl_pct <= 3:
        sw7 = 6
    elif sl_pct <= 4:
        sw7 = 4
    elif sl_pct <= 5:
        sw7 = 3
    else:
        sw7 = 1

    base_weekly_score = sw1 + sw2 + sw3 + sw4 + sw5 + sw6 + sw7

    # --- Velocity Engine (0-5) ---
    velocity_pts = 0
    if data["volatility_state"] == "contracting" and data["vol_ratio"] >= 1.3:
        velocity_pts = 5  # Compression → expansion
    elif data["volatility_state"] == "expanding":
        velocity_pts = 3
    elif data["momentum_1d"] > 2:
        velocity_pts = 2

    # --- Global Auto-Scoring ---
    selection_pct = selection_total  # Already 0-100
    pattern_pct = data["pattern_score"] * 10  # 0-10 → 0-100
    liquidity_pct = 0
    if data["phase"] == "accumulation":
        liquidity_pct = 70
    elif data["phase"] == "expansion":
        liquidity_pct = 60
    else:
        liquidity_pct = 40
    indicator_pct = 0
    if data["macd_crossover"]:
        indicator_pct += 50
    if rsi and 50 <= rsi <= 70:
        indicator_pct += 30
    if data["golden_cross"]:
        indicator_pct += 20
    fib_pct = 50  # Base (we have fib levels computed)
    if data["vwap"] and cmp > data["vwap"]:
        fib_pct += 30
    base_weekly_pct = (base_weekly_score / 40) * 100
    velocity_pct = (velocity_pts / 5) * 100
    sweep_penalty = 0

    raw_confluence = (
        selection_pct * 0.20 +
        pattern_pct * 0.15 +
        liquidity_pct * 0.10 +
        indicator_pct * 0.10 +
        fib_pct * 0.05 +
        base_weekly_pct * 0.30 +
        velocity_pct * 0.10 -
        sweep_penalty
    )

    # Sector adjustment
    sector_boost = 0
    if data["momentum_1m"] > 5:
        sector_boost = 4
    elif data["momentum_1m"] > 0:
        sector_boost = 2
    elif data["momentum_1m"] < -5:
        sector_boost = -2

    final_probability = max(0, min(100, round(raw_confluence + sector_boost * 2)))

    # Final bucket
    if final_probability >= 80:
        bucket = "A+ TRADE"
        verdict = "STRONG BUY"
    elif final_probability >= 65:
        bucket = "Strong"
        verdict = "BUY"
    elif final_probability >= 50:
        bucket = "Watchlist"
        verdict = "HOLD"
    elif final_probability >= 35:
        bucket = "Speculative"
        verdict = "WAIT"
    else:
        bucket = "Avoid"
        verdict = "AVOID"

    if verdict_override:
        verdict = verdict_override

    return {
        "model": "Swing AI v14 (legacy)",
        "selection_total": selection_total,
        "selection_grade": selection_grade,
        "selection_action": selection_action,
        "components": {
            "W1_weekly_trend": {"score": w1, "max": 20},
            "W2_daily_setup": {"score": w2, "max": 20},
            "W3_trigger_clarity": {"score": w3, "max": 10},
            "W4_volume_power": {"score": w4, "max": 15},
            "W5_volatility": {"score": w5, "max": 10},
            "W6_overhead_supply": {"score": w6, "max": 10},
            "W7_sector_momentum": {"score": w7, "max": 10},
            "W8_risk_quality": {"score": w8, "max": 5},
        },
        "base_weekly_score": base_weekly_score,
        "base_weekly_max": 40,
        "velocity_points": velocity_pts,
        "velocity_max": 5,
        "raw_confluence": round(raw_confluence, 1),
        "sector_boost": sector_boost,
        "final_probability": final_probability,
        "bucket": bucket,
        "verdict": verdict,
    }


# ─────────────────────────────────────────────────
# MODEL 3: KING v16 — Scanner (100) + Positional (30)
# ─────────────────────────────────────────────────

def _score_swing_ai_v12_2(data: dict) -> dict:
    """
    Swing AI v12.2: weekly momentum engine from the VUltimate doc.
    Focus on the mechanical weekly selection scanner + base weekly score + sector-adjusted probability.
    """
    cmp = data["cmp"]
    rsi = data["rsi"] or 50
    selection = _score_selection_scanner(data)
    selection_total = selection["selection_total"]
    selection_grade = selection["selection_grade"]
    components = selection["components"]
    w1 = components["W1_weekly_tailwind"]["score"]
    w2 = components["W2_daily_setup_quality"]["score"]
    w3 = components["W3_trigger_clarity"]["score"]
    sl_pct = data["sl_pct"]

    if data["extension_pct"] > 5:
        verdict_override = "SKIP"
    elif sl_pct > 5:
        verdict_override = "WAIT" if selection_total >= 70 else "SKIP"
    else:
        verdict_override = None

    if selection_total >= 85:
        selection_action = "TRADE-READY"
    elif selection_total >= 70:
        selection_action = "WATCH / TRIGGER"
    elif selection_total >= 55:
        selection_action = "PERFECT ENTRY ONLY"
    else:
        selection_action = "SKIP"

    sw1 = 7 if w1 >= 16 else 5 if w1 >= 12 else 3 if w1 >= 8 else 1
    sw2 = 7 if w2 >= 16 and w3 >= 8 else 5 if w2 >= 14 and w3 >= 7 else 3 if w2 >= 10 else 1
    sw3 = 0
    if data["phase"] == "accumulation":
        sw3 += 4
    elif data["phase"] == "expansion":
        sw3 += 3
    if data["delivery_proxy"] == "supportive":
        sw3 += 2
    sw3 = min(6, sw3)
    sw4 = 4 if data["overhead_supply"] in ("open_air", "light") else 2 if data["overhead_supply"] == "moderate" else 1
    sw5 = 0
    if data["macd_crossover"]:
        sw5 += 2
    if 55 <= rsi <= 68:
        sw5 += 2
    elif 50 <= rsi <= 75:
        sw5 += 1
    if data["ema_20"] and data["ema_50"] and data["ema_20"] > data["ema_50"]:
        sw5 += 1
    sw5 = min(5, sw5)
    sw6 = 0
    if data["daily_bias"] == "bullish":
        sw6 += 2
    if data["weekly_bias"] == "bullish":
        sw6 += 3
    sw6 = min(5, sw6)
    sw7 = 6 if sl_pct <= 3 else 4 if sl_pct <= 4 else 3 if sl_pct <= 5 else 1
    base_weekly_score = sw1 + sw2 + sw3 + sw4 + sw5 + sw6 + sw7

    velocity_pts = 5 if data["volatility_state"] == "contracting" and data["vol_ratio"] >= 1.3 else 3 if data["volatility_state"] == "expanding" else 2 if data["momentum_1d"] > 2 else 1
    selection_pct = selection_total
    pattern_pct = min(100, data["pattern_score"] * 10)
    liquidity_pct = min(100, sw3 * (100 / 6))
    indicator_pct = min(100, sw5 * 20 + (20 if data["golden_cross"] else 0))
    fib_pct = 50 + (30 if data["vwap"] and cmp > data["vwap"] else 0) + (20 if data.get("fib_levels") else 0)
    base_weekly_pct = (base_weekly_score / 40) * 100
    velocity_pct = (velocity_pts / 5) * 100
    sweep_risk = 3 if data["delivery_proxy"] == "supportive" else 5 if data["delivery_proxy"] == "neutral" else 7
    sweep_penalty = ((sweep_risk / 10) * 100) * 0.05

    raw_confluence = (
        selection_pct * 0.20 +
        pattern_pct * 0.15 +
        liquidity_pct * 0.10 +
        indicator_pct * 0.10 +
        fib_pct * 0.05 +
        base_weekly_pct * 0.30 +
        velocity_pct * 0.10 -
        sweep_penalty
    )

    sector_boost = 6 if (data.get("sector_momentum_score", 0) or 0) >= 8 else 4 if (data.get("sector_momentum_score", 0) or 0) >= 6 else 2 if (data.get("sector_momentum_score", 0) or 0) >= 4 else 0 if (data.get("sector_momentum_score", 0) or 0) >= 3 else -2
    final_probability = round(_clamp(raw_confluence + sector_boost * 2, 0, 100), 1)

    if final_probability >= 80:
        bucket = "A+ TRADE"
        verdict = "STRONG BUY"
    elif final_probability >= 65:
        bucket = "Strong"
        verdict = "BUY"
    elif final_probability >= 50:
        bucket = "Watchlist"
        verdict = "HOLD"
    elif final_probability >= 35:
        bucket = "Speculative"
        verdict = "WAIT"
    else:
        bucket = "Avoid"
        verdict = "AVOID"

    if verdict_override:
        verdict = verdict_override

    liquidity_gate = "PASS" if (data.get("value_10d_cr", 0) or 0) >= 50 and data.get("candle_cleanliness", 0) >= 4 and 50 <= cmp <= 3000 else "FAIL"
    return {
        "model": "Swing AI v12.2",
        "selection_total": selection_total,
        "selection_grade": selection_grade,
        "selection_action": selection_action,
        "components": components,
        "base_weekly_score": base_weekly_score,
        "base_weekly_max": 40,
        "velocity_points": velocity_pts,
        "velocity_max": 5,
        "raw_confluence": round(raw_confluence, 1),
        "sector_boost": sector_boost,
        "final_probability": final_probability,
        "bucket": bucket,
        "verdict": verdict,
        "liquidity_gate": liquidity_gate,
    }


def _score_swing_ai_v12_1(data: dict) -> dict:
    """
    Swing AI v12.1 hyper-confluence engine from the chart-only swing doc.
    Uses pattern/liquidity/indicator/fib/velocity/sweep blocks with a sector boost add-on.
    """
    classical_score = min(10, data.get("pattern_score", 0) or 0)
    harmonic_score = 4 if data.get("fib_levels") and data["pattern_name"] in ("cup_handle", "vcp", "bull_flag") else 0
    gann_score = 5 if data.get("gann_levels") else 0
    pattern_engine = round(((classical_score + harmonic_score + gann_score) / 25) * 100, 1)

    liquidity_score = 0
    if data["phase"] == "accumulation":
        liquidity_score += 4
    elif data["phase"] == "expansion":
        liquidity_score += 3
    if data["delivery_proxy"] == "supportive":
        liquidity_score += 2
    if data["overhead_supply"] in ("open_air", "light"):
        liquidity_score += 2
    if data["vol_ratio"] >= 1.2:
        liquidity_score += 2
    liquidity_score = min(10, liquidity_score)

    rsi = data["rsi"] or 50
    indicator_score = 0
    if 55 <= rsi <= 68:
        indicator_score += 5
    elif 50 <= rsi <= 75:
        indicator_score += 3
    if data["macd_crossover"]:
        indicator_score += 5
    elif data["macd_histogram"] and data["macd_histogram"] > 0:
        indicator_score += 3
    if data["ema_20"] and data["ema_50"] and data["ema_200"] and data["ema_20"] > data["ema_50"] > data["ema_200"]:
        indicator_score += 5
    elif data["ema_20"] and data["ema_50"] and data["ema_20"] > data["ema_50"]:
        indicator_score += 3
    if data["bb_width"] < 8:
        indicator_score += 5
    indicator_score = min(20, indicator_score)

    fib_avwap_score = 0
    if data.get("fib_levels"):
        fib_avwap_score += 4
    if data.get("vwap") and data["cmp"] > data["vwap"]:
        fib_avwap_score += 3
    if data.get("supports"):
        fib_avwap_score += 3
    fib_avwap_score = min(10, fib_avwap_score)

    weekly_strength = 7 if data["weekly_structure"] == "HH/HL" else 4 if data["weekly_structure"] == "range" else 1
    breakout_validity = 7 if data.get("trigger") and data.get("invalidation") and data.get("extension_pct", 0) <= 2 else 5 if data.get("trigger") else 2
    smart_money_quality = 7 if liquidity_score >= 8 else 5 if liquidity_score >= 5 else 2
    vrvp_pocket = 5 if data["overhead_supply"] in ("open_air", "light") else 3 if data["overhead_supply"] == "moderate" else 1
    indicator_alignment = 5 if indicator_score >= 15 else 3 if indicator_score >= 10 else 1
    risk_fit = 5 if data["sl_pct"] <= 3.5 else 3 if data["sl_pct"] <= 5 else 1
    pattern_bonus = 4 if pattern_engine >= 70 else 2 if pattern_engine >= 45 else 1
    core_swing_points = min(40, weekly_strength + breakout_validity + smart_money_quality + vrvp_pocket + indicator_alignment + risk_fit + pattern_bonus)

    velocity_score = 10 if data["volatility_state"] == "expanding" and data["vol_ratio"] >= 1.5 else 8 if data["volatility_state"] == "contracting" and data["vol_ratio"] >= 1.2 else 5 if data["momentum_1w"] > 3 else 3
    sweep_risk = 2 if data["delivery_proxy"] == "supportive" and data["phase"] == "accumulation" else 5 if data["delivery_proxy"] == "neutral" else 7
    liquidity_sweep_score = max(0, 10 - sweep_risk)

    base_probability = round((core_swing_points / 40) * 100, 1)
    sector_boost_applied = (data.get("sector_momentum_score", 0) or 0) >= 4
    sector_boost_impact = 12 if (data.get("sector_momentum_score", 0) or 0) >= 8 else 10 if (data.get("sector_momentum_score", 0) or 0) >= 6 else 6 if sector_boost_applied else 0
    final_probability = round(min(100, base_probability + sector_boost_impact), 1)
    verdict = _verdict_from_probability(final_probability)
    if data.get("extension_pct", 0) > 5:
        verdict = "AVOID"
    elif data.get("extension_pct", 0) > 3:
        verdict = "WAIT"

    return {
        "model": "Swing AI v12.1",
        "scanner_score": round(core_swing_points * 2.5, 1),
        "selection_total": round(core_swing_points * 2.5, 1),
        "components": {
            "P1_pattern_engine": {"score": round(pattern_engine, 1), "max": 100},
            "P2_liquidity": {"score": liquidity_score, "max": 10},
            "P3_indicator": {"score": indicator_score, "max": 20},
            "P4_fib_avwap": {"score": fib_avwap_score, "max": 10},
            "P5_velocity": {"score": velocity_score, "max": 10},
            "P6_sweep": {"score": liquidity_sweep_score, "max": 10},
            "P7_core_swing": {"score": core_swing_points, "max": 40},
        },
        "pattern_engine": round(pattern_engine, 1),
        "liquidity_score": liquidity_score,
        "indicator_score": indicator_score,
        "fib_avwap_score": fib_avwap_score,
        "velocity_score": velocity_score,
        "liquidity_sweep_score": liquidity_sweep_score,
        "base_probability": base_probability,
        "sector_boost_applied": sector_boost_applied,
        "sector_boost_impact": sector_boost_impact,
        "final_probability": final_probability,
        "verdict": verdict,
        "selection_grade": "A+" if final_probability >= 85 else "A" if final_probability >= 70 else "B" if final_probability >= 55 else "SKIP",
        "selection_action": "TRADE" if verdict in ("BUY", "STRONG BUY") else "WAIT RETEST" if verdict in ("HOLD", "WAIT") else "SKIP",
    }


def _score_king(data: dict) -> dict:
    """
    KING v16: Combined scanner + pattern engine + smart money + backtest.
    Focus on VRVP/AVWAP runway and dual trade plans.
    """
    cmp = data["cmp"]
    rsi = data["rsi"] or 50
    sl_pct = data["sl_pct"]

    # S1: Multi-TF Trend Power (0-20)
    s1 = 0
    if data["weekly_structure"] == "HH/HL":
        s1 += 8
    elif data["weekly_structure"] == "range":
        s1 += 4
    if data["ema_50"] and cmp > data["ema_50"]:
        s1 += 4
    if data["ema_200"] and cmp > data["ema_200"]:
        s1 += 4
    if rsi >= 55:
        s1 += 4
    elif rsi >= 50:
        s1 += 2
    s1 = min(20, s1)

    # S2: Setup Quality (0-25)
    s2 = 0
    pn = data["pattern_name"]
    if pn in ("vcp", "cup_handle", "ascending_triangle"):
        s2 += 18
    elif pn in ("bull_flag", "base_breakout"):
        s2 += 14
    elif pn in ("pullback_ema20", "trendline_break"):
        s2 += 10
    elif pn == "box":
        s2 += 7
    else:
        s2 += 2
    if data["trigger"] and data["invalidation"]:
        s2 += 4
    if data["candle_cleanliness"] >= 7:
        s2 += 3
    s2 = min(25, s2)

    # S3: Volume + Delivery (0-15)
    s3 = 0
    if data["vol_ratio"] >= 1.5:
        s3 += 8
    elif data["vol_ratio"] >= 1.2:
        s3 += 5
    if data["base_contraction"]:
        s3 += 4
    if data["delivery_proxy"] == "supportive":
        s3 += 3
    s3 = min(15, s3)

    # S4: VRVP + AVWAP Runway (0-15)
    s4 = 0
    if data["vwap"] and cmp > data["vwap"]:
        s4 += 5
    if data["overhead_supply"] == "open_air":
        s4 += 10
    elif data["overhead_supply"] == "light":
        s4 += 7
    elif data["overhead_supply"] == "moderate":
        s4 += 4
    s4 = min(15, s4)

    # S5: Indicator Stack (0-15)
    s5 = 0
    if rsi and 55 <= rsi <= 68:
        s5 += 5
    elif rsi and 50 <= rsi <= 75:
        s5 += 3
    if data["macd_crossover"]:
        s5 += 5
    elif data["macd_histogram"] and data["macd_histogram"] > 0:
        s5 += 2
    if (data["ema_20"] and data["ema_50"] and data["ema_200"]
            and data["ema_20"] > data["ema_50"] > data["ema_200"]):
        s5 += 5
    elif data["ema_20"] and data["ema_50"] and data["ema_20"] > data["ema_50"]:
        s5 += 3
    s5 = min(15, s5)

    # S6: Risk-Reward Fit (0-10)
    s6 = 0
    if 0 < sl_pct <= 3.5:
        s6 = 10
    elif sl_pct <= 4.5:
        s6 = 7
    elif sl_pct <= 5:
        s6 = 4
    else:
        s6 = 1

    raw_score = s1 + s2 + s3 + s4 + s5 + s6

    # Penalties
    penalties = 0
    penalty_reasons = []
    if data["extension_pct"] > 4:
        penalties += 10
        penalty_reasons.append("CMP >4% above trigger")
    elif data["extension_pct"] > 2:
        penalties += 5
        penalty_reasons.append("CMP >2% above trigger")
    if data["overhead_supply"] == "heavy" and data["vol_ratio"] < 1.5:
        penalties += 7
        penalty_reasons.append("Heavy supply without thrust")
    if data["candle_cleanliness"] < 4:
        penalties += 5
        penalty_reasons.append("Dirty gaps/spikes")

    final_scanner_score = max(0, raw_score - penalties)

    # Probability map
    if final_scanner_score >= 90:
        probability = 88
    elif final_scanner_score >= 80:
        probability = 79
    elif final_scanner_score >= 70:
        probability = 69
    elif final_scanner_score >= 60:
        probability = 59
    else:
        probability = 45

    # Positional Score (0-30)
    p1 = 5 if data["weekly_bias"] == "bullish" else 3 if data["weekly_structure"] == "range" else 1
    p2 = min(5, data["pattern_score"] // 2 + 1)
    p3 = 5 if data["phase"] == "accumulation" else 3 if data["phase"] == "expansion" else 1
    p4 = 5 if data["overhead_supply"] in ("open_air", "light") else 3 if data["overhead_supply"] == "moderate" else 1
    p5 = 5 if data["daily_bias"] == "bullish" and data["weekly_bias"] == "bullish" else 3 if data["daily_bias"] == "bullish" else 1
    p6 = 5 if sl_pct <= 3.5 else 3 if sl_pct <= 5 else 1

    positional_score = p1 + p2 + p3 + p4 + p5 + p6

    # Classification
    if positional_score >= 26:
        pos_class = "HIGH-CONVICTION 15-25%"
    elif positional_score >= 21:
        pos_class = "Strong Positional"
    elif positional_score >= 16:
        pos_class = "Conditional / WAIT"
    else:
        pos_class = "Avoid Positional"

    # Verdict
    if data["extension_pct"] > 4:
        verdict = "AVOID"
    elif data["extension_pct"] > 2:
        verdict = "WAIT"
    elif sl_pct > 4.5:
        verdict = "WAIT"
    elif final_scanner_score >= 80:
        verdict = "BUY" if final_scanner_score < 85 else "STRONG BUY"
    elif final_scanner_score >= 70:
        verdict = "HOLD"
    elif final_scanner_score >= 60:
        verdict = "WAIT"
    else:
        verdict = "AVOID"

    return {
        "model": "KING v16",
        "scanner_score": final_scanner_score,
        "scanner_raw": raw_score,
        "penalties": penalties,
        "penalty_reasons": penalty_reasons,
        "components": {
            "S1_trend_power": {"score": s1, "max": 20},
            "S2_setup_quality": {"score": s2, "max": 25},
            "S3_volume_delivery": {"score": s3, "max": 15},
            "S4_vrvp_runway": {"score": s4, "max": 15},
            "S5_indicator_stack": {"score": s5, "max": 15},
            "S6_risk_reward": {"score": s6, "max": 10},
        },
        "positional_score": positional_score,
        "positional_max": 30,
        "positional_class": pos_class,
        "probability_pct": probability,
        "verdict": verdict,
    }


# ─────────────────────────────────────────────────
# TRADE PLAN GENERATOR
# ─────────────────────────────────────────────────

def _generate_trade_plan(data: dict) -> dict:
    """Generate trade plans (scanner + positional) from raw data."""
    cmp = data["cmp"]
    trigger = data["trigger"]
    invalidation = data["invalidation"]
    sl_pct = data["sl_pct"]

    # Scanner Trade Plan (10-15%)
    t1_pct = 8
    t2_pct = 12
    t3_pct = 15
    t1 = round(trigger * (1 + t1_pct / 100), 2)
    t2 = round(trigger * (1 + t2_pct / 100), 2)
    t3 = round(trigger * (1 + t3_pct / 100), 2)

    rr_t1 = round(t1_pct / sl_pct, 2) if sl_pct > 0 else 0
    rr_t2 = round(t2_pct / sl_pct, 2) if sl_pct > 0 else 0

    # Action
    ext = data["extension_pct"]
    if sl_pct > 5 and cmp < trigger:
        action = "ALERT ONLY"
    elif sl_pct > 5:
        action = "SKIP"
    elif cmp < trigger:
        action = "ALERT ONLY"
    elif ext > 5:
        action = "SKIP"
    elif ext > 3:
        action = "NO CHASE"
    elif ext > 2:
        action = "WAIT RETEST"
    else:
        action = "TRADE"

    # Retest zone
    retest_low = round(trigger * 0.98, 2)
    retest_high = round(trigger * 1.01, 2)

    scanner_plan = {
        "mode": "Scanner (10-15%)",
        "entry_breakout": trigger,
        "entry_retest_zone": [retest_low, retest_high],
        "stop_loss": invalidation,
        "sl_pct": sl_pct,
        "targets": {
            "T1": {"price": t1, "pct": t1_pct},
            "T2": {"price": t2, "pct": t2_pct},
            "T3": {"price": t3, "pct": t3_pct},
        },
        "rr_t1": rr_t1,
        "rr_t2": rr_t2,
        "action": action,
        "trail_rule": "Below EMA20 or last HL",
    }

    # Positional Trade Plan (15-25%)
    p1_pct = 16
    p2_pct = 22
    p1 = round(trigger * (1 + p1_pct / 100), 2)
    p2 = round(trigger * (1 + p2_pct / 100), 2)

    positional_plan = {
        "mode": "Positional (15-25%)",
        "entry_zone": [retest_low, retest_high],
        "stop_loss": invalidation,
        "sl_pct": sl_pct,
        "targets": {
            "P1": {"price": p1, "pct": p1_pct},
            "P2": {"price": p2, "pct": p2_pct},
        },
        "hold_rule": "EMA20 / AVWAP / last HL",
        "invalidation_note": f"Close below ₹{invalidation}",
    }

    return {
        "scanner_plan": scanner_plan,
        "positional_plan": positional_plan,
    }


def _fmt_rupees(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"₹{value:,.0f}" if abs(value) >= 100 else f"₹{value:,.2f}"


def _fmt_rupee_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "—"
    return f"{_fmt_rupees(low)}–{_fmt_rupees(high)}"


def _generate_one_line_verdict(
    symbol: str,
    raw: dict,
    titan: dict,
    swing_ai: dict,
    king: dict,
    composite: dict,
    trade_plans: dict,
) -> str:
    verdict = composite["consensus_verdict"]
    action = trade_plans["scanner_plan"]["action"]
    trigger = raw.get("trigger")
    cmp = raw.get("cmp")
    sl = raw.get("invalidation")
    retest = trade_plans["scanner_plan"].get("entry_retest_zone") or []
    pattern = (raw.get("pattern_name") or "setup").replace("_", " ")
    composite_score = composite.get("composite_score", 0) or 0

    setup_bits: list[str] = []
    caution_bits: list[str] = []

    if cmp is not None and trigger:
        if cmp >= trigger:
            setup_bits.append("has triggered the breakout")
        elif trigger - cmp <= max(trigger * 0.02, 2):
            setup_bits.append("is sitting just below the breakout trigger")
        else:
            setup_bits.append("is still below the breakout trigger")

    if raw.get("weekly_structure") == "HH/HL":
        setup_bits.append("weekly structure is bullish")
    elif raw.get("daily_bias") == "bullish":
        setup_bits.append("daily trend is supportive")

    if raw.get("pattern_name") in ("cup_handle", "vcp", "bull_flag", "ascending_triangle", "base_breakout"):
        setup_bits.append(f"the {pattern} setup is valid")

    if raw.get("vol_ratio", 0) >= 1.5:
        setup_bits.append(f"volume is strong at {raw['vol_ratio']:.1f}x")

    if raw.get("delivery_proxy") == "weak":
        caution_bits.append("latest delivery is weak")
    elif raw.get("delivery_trend") == "falling":
        caution_bits.append("delivery trend is falling")

    if raw.get("upper_wick_heavy"):
        caution_bits.append("the latest candle shows an upper wick")

    if raw.get("overhead_supply") == "heavy":
        caution_bits.append("overhead supply is heavy")
    elif raw.get("overhead_supply") == "moderate":
        caution_bits.append("nearby resistance is still overhead")

    if raw.get("sl_pct", 0) > 5:
        caution_bits.append(f"the risk is wide at {raw['sl_pct']:.1f}%")

    if raw.get("news_tone") == "Negative":
        caution_bits.append("news tone is negative")
    elif raw.get("retail_psych") == "FOMO":
        caution_bits.append("price is attracting FOMO")

    if titan.get("liquidity_gate") == "FAIL":
        caution_bits.append("liquidity gate is not clean")

    lead = setup_bits[0] if setup_bits else f"is rated {verdict.lower()}"
    second = setup_bits[1] if len(setup_bits) > 1 else None
    caution = None
    if len(caution_bits) >= 2:
        caution = f"{caution_bits[0]} and {caution_bits[1]}"
    elif caution_bits:
        caution = caution_bits[0]

    entry_phrase = ""
    if action in ("WAIT RETEST", "NO CHASE", "ALERT ONLY") and len(retest) == 2:
        entry_phrase = f"wait for the {_fmt_rupee_range(retest[0], retest[1])} retest"
    elif action == "TRADE" and trigger:
        entry_phrase = f"trade above {_fmt_rupees(trigger)}"
    elif action == "SKIP":
        entry_phrase = "skip fresh entries for now"
    else:
        entry_phrase = "stay on watch"

    risk_phrase = ""
    if sl:
        if action in ("TRADE", "ALERT ONLY", "WAIT RETEST", "NO CHASE"):
            risk_phrase = f"with strict SL {_fmt_rupees(sl)}"
        else:
            risk_phrase = f"while respecting SL {_fmt_rupees(sl)}"

    parts: list[str] = [f"{symbol} {lead}"]
    if second:
        parts[-1] += f", and {second}"
    if caution:
        parts[-1] += f", but {caution}"

    aggressive_ok = (
        verdict == "WAIT"
        and action in ("ALERT ONLY", "WAIT RETEST", "NO CHASE")
        and composite_score >= 68
        and titan.get("selection_grade") in ("A+", "A", "B")
        and raw.get("pattern_name") in ("cup_handle", "vcp", "bull_flag", "ascending_triangle", "base_breakout", "pullback_ema20")
    )
    aggressive_range = ""
    if aggressive_ok and cmp:
        small_entry_low = round(cmp * 0.995, 2)
        small_entry_high = round(cmp * 1.005, 2)
        aggressive_range = _fmt_rupee_range(small_entry_low, small_entry_high)

    if verdict in ("BUY", "STRONG BUY") and action == "TRADE":
        action_text = f"best trade is to {entry_phrase}"
    elif verdict == "WAIT":
        action_text = f"best trade is to {entry_phrase}"
        if aggressive_range:
            action_text += f"; aggressive traders can take only small quantity near {aggressive_range}"
    elif verdict == "AVOID":
        action_text = f"best trade is to {entry_phrase}"
    else:
        action_text = f"best trade is to {entry_phrase}"

    parts.append(action_text)
    if risk_phrase:
        parts[-1] += f" {risk_phrase}"

    return "; ".join(parts) + "."


# ─────────────────────────────────────────────────
# COMPOSITE ACROSS ALL 3 MODELS
# ─────────────────────────────────────────────────

def _compute_composite(models: dict[str, dict]) -> dict:
    """Compute the weighted composite across TITAN v20, TITAN v19, both Swing AI engines, and KING."""
    score_map = {
        "TITAN": models["titan"]["scanner_score"],
        "TITAN_v19": models["titan_v19"]["scanner_score"],
        "Swing_AI": models["swing_ai_v12_2"]["selection_total"],
        "Swing_AI_Hyper": models["swing_ai_v12_1"]["selection_total"],
        "KING": models["king"]["scanner_score"],
    }
    probability_map = {
        "TITAN": models["titan"]["probability_pct"],
        "TITAN_v19": models["titan_v19"]["probability_pct"],
        "Swing_AI": models["swing_ai_v12_2"]["final_probability"],
        "Swing_AI_Hyper": models["swing_ai_v12_1"]["final_probability"],
        "KING": models["king"]["probability_pct"],
    }
    verdict_map = {
        "TITAN": models["titan"]["verdict"],
        "TITAN_v19": models["titan_v19"]["verdict"],
        "Swing_AI": models["swing_ai_v12_2"]["verdict"],
        "Swing_AI_Hyper": models["swing_ai_v12_1"]["verdict"],
        "KING": models["king"]["verdict"],
    }

    composite_score = round(sum(score_map[key] * MODEL_WEIGHTS[key] for key in MODEL_WEIGHTS), 1)
    composite_prob = round(sum(probability_map[key] * MODEL_WEIGHTS[key] for key in MODEL_WEIGHTS), 1)

    verdict_priority = {
        "STRONG BUY": 5,
        "BUY": 4,
        "HOLD": 3,
        "WAIT": 2,
        "AVOID": 1,
        "SKIP": 0,
    }
    weighted_verdict_score = sum(verdict_priority.get(verdict_map[key], 2) * MODEL_WEIGHTS[key] for key in MODEL_WEIGHTS)

    if weighted_verdict_score >= 4.5:
        consensus_verdict = "STRONG BUY"
    elif weighted_verdict_score >= 3.5:
        consensus_verdict = "BUY"
    elif weighted_verdict_score >= 2.5:
        consensus_verdict = "HOLD"
    elif weighted_verdict_score >= 1.5:
        consensus_verdict = "WAIT"
    else:
        consensus_verdict = "AVOID"

    unique_verdicts = len(set(verdict_map.values()))
    if unique_verdicts == 1:
        agreement = "UNANIMOUS"
    elif len([v for v in verdict_map.values() if v == consensus_verdict]) >= 3:
        agreement = "MAJORITY"
    else:
        agreement = "SPLIT"

    model_scores = {
        **score_map,
        "TITAN_v20": score_map["TITAN"],
    }
    model_verdicts = {
        **verdict_map,
        "TITAN_v20": verdict_map["TITAN"],
    }

    return {
        "composite_score": composite_score,
        "composite_probability": composite_prob,
        "consensus_verdict": consensus_verdict,
        "agreement": agreement,
        "model_verdicts": model_verdicts,
        "model_scores": model_scores,
        "model_weights": MODEL_WEIGHTS,
    }


# ─────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────

def run_mate_pro_analysis(
    db: Session,
    symbol: str,
    mode: str = "full",
    allow_llm_verdict: bool = True,
) -> dict | None:
    """
    Run all 3 MATE-PRO models on a stock and return comprehensive results.
    Returns None if insufficient data.
    """
    logger.info(f"Running MATE-PRO analysis for {symbol}...")

    # Extract raw data
    raw = _extract_raw_data(db, symbol, mode=mode)
    if not raw:
        logger.warning(f"Insufficient data for MATE-PRO analysis of {symbol}")
        return None

    # Run all weighted engines
    titan = _score_titan_v20(raw)
    titan_v19 = _score_titan_v19(raw)
    swing_ai = _score_swing_ai_v12_2(raw)
    swing_ai_hyper = _score_swing_ai_v12_1(raw)
    king = _score_king(raw)

    # Generate trade plans
    trade_plans = _generate_trade_plan(raw)

    # Compute composite
    composite = _compute_composite({
        "titan": titan,
        "titan_v19": titan_v19,
        "swing_ai_v12_2": swing_ai,
        "swing_ai_v12_1": swing_ai_hyper,
        "king": king,
    })
    fallback_one_line_verdict = _generate_one_line_verdict(symbol, raw, titan, swing_ai, king, composite, trade_plans)
    if allow_llm_verdict:
        one_line_verdict, one_line_verdict_source = generate_llm_one_line_verdict(
            symbol,
            raw,
            titan,
            swing_ai,
            king,
            composite,
            trade_plans,
            fallback_one_line_verdict,
        )
    else:
        one_line_verdict, one_line_verdict_source = fallback_one_line_verdict, "rules"

    result = _to_python({
        "symbol": symbol,
        "cmp": raw["cmp"],
        "timestamp": datetime.now().isoformat(),
        "one_line_verdict": one_line_verdict,
        "one_line_verdict_source": one_line_verdict_source,

        # Market context
        "context": {
            "daily_structure": raw["daily_structure"],
            "weekly_structure": raw["weekly_structure"],
            "daily_bias": raw["daily_bias"],
            "weekly_bias": raw["weekly_bias"],
            "phase": raw["phase"],
            "volatility_state": raw["volatility_state"],
            "pattern": raw["pattern_name"],
            "overhead_supply": raw["overhead_supply"],
            "sector": raw["sector"],
            "industry": raw["industry"],
            "sector_index": raw["sector_index"],
            "sector_weekly_rsi": raw["sector_weekly_rsi"],
            "sector_structure": raw["sector_structure"],
            "sector_peers": raw["sector_peers"],
            "sector_peer_breakouts": raw["sector_peer_breakouts"],
            "sector_positive_peers": raw.get("sector_positive_peers"),
            "sector_peer_avg_perf_1m": raw.get("sector_peer_avg_perf_1m"),
            "sector_perf_1m": raw.get("sector_perf_1m"),
            "sector_perf_3m": raw.get("sector_perf_3m"),
            "sector_perf_6m": raw.get("sector_perf_6m"),
            "sector_momentum_score": raw.get("sector_momentum_score"),
            "news_tone": raw["news_tone"],
            "nifty_trend_state": raw["nifty_trend_state"],
            "nifty_weekly_rsi": raw["nifty_weekly_rsi"],
            "retail_psych": raw["retail_psych"],
            "delivery_10d_avg_pct": raw["delivery_10d_avg_pct"],
            "delivery_trend": raw["delivery_trend"],
            "corporate_action_note": raw["corporate_action_note"],
            "missing_data": raw["missing_data"],
        },

        # Key metrics
        "metrics": {
            "high_52w": raw["high_52w"],
            "pct_from_52w": raw["pct_from_52w"],
            "rsi": raw["rsi"],
            "macd_crossover": raw["macd_crossover"],
            "ema_stack": "bullish" if (raw["ema_20"] and raw["ema_50"] and raw["ema_200"]
                                       and raw["ema_20"] > raw["ema_50"] > raw["ema_200"]) else "mixed",
            "vol_ratio": raw["vol_ratio"],
            "atr_pct": raw["atr_pct"],
            "sl_pct": raw["sl_pct"],
            "value_10d_cr": raw["value_10d_cr"],
            "vol_10d": raw["vol_10d"],
            "delivery_10d_avg_pct": raw["delivery_10d_avg_pct"],
            "delivery_trend": raw["delivery_trend"],
            "sentiment_score": raw["sentiment_score"],
        },

        # Levels
        "levels": {
            "trigger": raw["trigger"],
            "invalidation": raw["invalidation"],
            "supports": raw["supports"],
            "resistances": raw["resistances"],
        },

        # Model scores
        "models": {
            "titan": titan,
            "titan_v19": titan_v19,
            "swing_ai_v12_2": swing_ai,
            "swing_ai_v12_1": swing_ai_hyper,
            "king": king,
        },

        # Composite
        "composite": composite,

        # Trade plans
        "trade_plans": trade_plans,
    })

    logger.info(
        f"MATE-PRO {symbol}: Composite={composite['composite_score']}/100, "
        f"Verdict={composite['consensus_verdict']}, Agreement={composite['agreement']}"
    )

    return result


def run_mate_pro_batch(
    db: Session,
    symbols: list[str],
    mode: str = "batch",
    allow_llm_verdict: bool = False,
) -> list[dict]:
    """Run MATE-PRO analysis on a batch of symbols. Returns sorted list."""
    results = []
    preload_stock_profiles(db, symbols)
    if mode == "full":
        preload_news_tones(db, symbols)
    for sym in symbols:
        try:
            result = run_mate_pro_analysis(db, sym, mode=mode, allow_llm_verdict=allow_llm_verdict)
            if result:
                results.append(result)
        except Exception as e:
            logger.error(f"MATE-PRO failed for {sym}: {e}")

    # Sort by composite score descending
    results.sort(key=lambda x: x["composite"]["composite_score"], reverse=True)

    # Add ranks
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


def extract_actionable(mate_pro_results: list[dict], top_n: int = 20) -> list[dict]:
    """
    Extract actionable stocks from MATE-PRO results.
    Returns stocks where you can ACT now:
      - BUY: STRONG BUY or BUY verdict with TRADE action
      - SHORT SELL: AVOID verdict with bearish setup (death cross, bearish bias, breakdown)
    Each entry has an 'action_type' field: 'BUY' or 'SHORT SELL'
    """
    actionable = []

    for r in mate_pro_results:
        comp = r["composite"]
        verdict = comp["consensus_verdict"]
        score = comp["composite_score"]
        probability = comp["composite_probability"]
        action = r["trade_plans"]["scanner_plan"]["action"]
        context = r.get("context", {})
        metrics = r.get("metrics", {})

        entry = None

        # ── BUY candidates ──
        # STRONG BUY or BUY verdict, scanner action is TRADE or WAIT RETEST
        if verdict in ("STRONG BUY", "BUY") and action in ("TRADE", "WAIT RETEST"):
            # Build human-readable reason
            reasons = []
            if comp["agreement"] == "UNANIMOUS":
                reasons.append("All 5 engines agree")
            elif comp["agreement"] == "MAJORITY":
                reasons.append("Most weighted engines agree")
            phase = context.get("phase", "")
            if phase in ("accumulation", "markup"):
                reasons.append(f"{phase.title()} phase")
            ema_stack = metrics.get("ema_stack", "")
            if ema_stack == "bullish":
                reasons.append("Bullish EMA stack")
            rsi_val = metrics.get("rsi")
            if rsi_val and 40 <= rsi_val <= 60:
                reasons.append(f"RSI neutral ({rsi_val:.0f})")
            elif rsi_val and rsi_val < 40:
                reasons.append(f"RSI oversold ({rsi_val:.0f})")
            vol_r = metrics.get("vol_ratio", 0)
            if vol_r and vol_r >= 1.5:
                reasons.append(f"High volume ({vol_r:.1f}x)")
            pattern = context.get("pattern", "")
            if pattern:
                reasons.append(pattern.replace("_", " ").title())
            daily_struct = context.get("daily_structure", "")
            if daily_struct == "higher_highs":
                reasons.append("Higher highs")

            entry = {
                "symbol": r["symbol"],
                "cmp": r["cmp"],
                "action_type": "BUY",
                "verdict": verdict,
                "composite_score": score,
                "probability": probability,
                "agreement": comp["agreement"],
                "model_scores": comp["model_scores"],
                "scanner_action": action,
                "trigger": r["levels"]["trigger"],
                "stop_loss": r["levels"]["invalidation"],
                "sl_pct": metrics.get("sl_pct", 0),
                "targets": r["trade_plans"]["scanner_plan"]["targets"],
                "rr_t2": r["trade_plans"]["scanner_plan"].get("rr_t2", 0),
                "pattern": pattern,
                "phase": phase,
                "rsi": rsi_val,
                "ema_stack": ema_stack,
                "vol_ratio": vol_r,
                "reason": " · ".join(reasons[:4]) if reasons else "Strong composite score",
                "one_line_verdict": r.get("one_line_verdict"),
                "one_line_verdict_source": r.get("one_line_verdict_source"),
                # Sort priority: higher score = better buy
                "_sort_key": score,
            }

        # ── SHORT SELL candidates ──
        # AVOID verdict with bearish confirmation signals
        elif verdict == "AVOID" and score <= 35:
            daily_bias = context.get("daily_bias", "")
            weekly_bias = context.get("weekly_bias", "")
            death_cross = r.get("levels", {})  # Check from raw data
            rsi = metrics.get("rsi", 50)
            phase = context.get("phase", "")

            # Need at least 2 bearish confirmations
            bearish_signals = 0
            if daily_bias == "bearish":
                bearish_signals += 1
            if weekly_bias == "bearish":
                bearish_signals += 1
            if rsi and rsi > 65:  # Overbought = ripe for short
                bearish_signals += 1
            if phase in ("distribution", "markdown"):
                bearish_signals += 1
            if metrics.get("ema_stack") != "bullish":
                bearish_signals += 1

            if bearish_signals >= 2:
                # Build human-readable reason
                short_reasons = []
                if daily_bias == "bearish":
                    short_reasons.append("Bearish daily bias")
                if weekly_bias == "bearish":
                    short_reasons.append("Bearish weekly bias")
                if rsi and rsi > 65:
                    short_reasons.append(f"Overbought RSI ({rsi:.0f})")
                if phase in ("distribution", "markdown"):
                    short_reasons.append(f"{phase.title()} phase")
                if metrics.get("ema_stack") != "bullish":
                    short_reasons.append("Broken EMA stack")
                pattern = context.get("pattern", "")
                if pattern:
                    short_reasons.append(pattern.replace("_", " ").title())

                entry = {
                    "symbol": r["symbol"],
                    "cmp": r["cmp"],
                    "action_type": "SHORT SELL",
                    "verdict": verdict,
                    "composite_score": score,
                    "probability": 100 - probability,  # Invert for short
                    "agreement": comp["agreement"],
                    "model_scores": comp["model_scores"],
                    "scanner_action": "SHORT",
                    "trigger": r["levels"]["invalidation"],  # Short below support
                    "stop_loss": r["levels"]["trigger"],  # Stop above resistance
                    "sl_pct": metrics.get("sl_pct", 0),
                    "targets": {
                        "T1": {"price": round(r["cmp"] * 0.95, 2), "pct": -5},
                        "T2": {"price": round(r["cmp"] * 0.90, 2), "pct": -10},
                        "T3": {"price": round(r["cmp"] * 0.85, 2), "pct": -15},
                    },
                    "rr_t2": round(10 / max(metrics.get("sl_pct", 5), 1), 2),
                    "pattern": pattern,
                    "phase": phase,
                    "rsi": rsi,
                    "ema_stack": metrics.get("ema_stack", ""),
                    "vol_ratio": metrics.get("vol_ratio", 0),
                    "bearish_signals": bearish_signals,
                    "reason": " · ".join(short_reasons[:4]) if short_reasons else "Weak composite score with bearish setup",
                    "one_line_verdict": r.get("one_line_verdict"),
                    "one_line_verdict_source": r.get("one_line_verdict_source"),
                    # Sort priority: lower score = stronger short
                    "_sort_key": 100 - score,
                }

        if entry:
            titan = r.get("models", {}).get("titan", {})
            titan_v19 = r.get("models", {}).get("titan_v19", {})
            # Add mate_pro sub-dict matching StockTable's expected format
            entry["mate_pro"] = {
                "model_scores": entry["model_scores"],
                "composite_score": entry["composite_score"],
                "consensus_verdict": entry["verdict"],
                "composite_probability": entry["probability"],
                "one_line_verdict": entry.get("one_line_verdict"),
                "one_line_verdict_source": entry.get("one_line_verdict_source"),
                "action": entry["scanner_action"],
                "titan_v20": {
                    "model": titan.get("model"),
                    "liquidity_gate": titan.get("liquidity_gate"),
                    "selection_grade": titan.get("selection_grade"),
                    "selection_action": titan.get("selection_action"),
                    "setup_family": titan.get("setup_family"),
                    "sector_momentum_score": ((titan.get("sector_context") or {}).get("sector_momentum_score")),
                    "sector_index": ((titan.get("sector_context") or {}).get("sector_index")),
                    "sector_weekly_rsi": ((titan.get("sector_context") or {}).get("sector_weekly_rsi")),
                    "news_tone": ((titan.get("sentiment_filter") or {}).get("news_tone")),
                    "market_mood": ((titan.get("sentiment_filter") or {}).get("nifty_mood")),
                    "retail_psych": ((titan.get("sentiment_filter") or {}).get("retail_psych")),
                },
                "titan_v19": {
                    "model": titan_v19.get("model"),
                    "liquidity_gate": titan_v19.get("liquidity_gate"),
                    "selection_grade": titan_v19.get("selection_grade"),
                    "selection_action": titan_v19.get("selection_action"),
                    "setup_family": titan_v19.get("setup_family"),
                },
            }
            actionable.append(entry)

    # Sort: BUY first (by score desc), then SHORT (by inverse score desc)
    buys = sorted([a for a in actionable if a["action_type"] == "BUY"],
                  key=lambda x: x["_sort_key"], reverse=True)
    shorts = sorted([a for a in actionable if a["action_type"] == "SHORT SELL"],
                    key=lambda x: x["_sort_key"], reverse=True)

    # Return ALL actionable stocks (pagination handled in frontend)
    # top_n=0 means no limit; otherwise cap each category
    if top_n and top_n > 0:
        result = buys[:top_n] + shorts[:top_n]
    else:
        result = buys + shorts

    # Clean up internal sort key
    for r in result:
        r.pop("_sort_key", None)

    logger.info(f"Actionable: {len(buys)} BUY candidates, {len(shorts)} SHORT candidates")
    return result
