"""
MATE-PRO Scoring Engine.
Implements the active MATE-PRO engines as mechanical scoring systems:
  1. TITAN v20
  2. TITAN v19
  3. Swing AI v12.2
  4. Swing AI v12.1
  5. KING v16
  6. JP Pattern Engine v1
  Shared Backtest Validation (combined report; excluded from final verdict weightage)

Each model takes the same raw technical data from our analysis engine and
applies its own scoring formula to produce scores, verdicts, and trade plans.
"""
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import DailyCandle, DeliveryData, Stock
from app.services.llm_verdict import generate_llm_one_line_verdict
from app.services.market_context import (
    build_market_context,
    preload_delivery_contexts,
    preload_news_tones,
    preload_stock_profiles,
)
from app.services.technical import (
    _calc_ema, _calc_sma, _calc_rsi, _calc_bollinger, _calc_macd,
    _calc_vwap, _calc_fibonacci_levels, _calc_gann_levels,
    _calc_volume_profile, _detect_golden_death_cross,
)
from app.config import MATE_PRO_BATCH_PRELOAD_PROFILES, MATE_PRO_BATCH_WORKERS, NSE_SUFFIX

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

BACKTEST_LOOKBACK_DAYS = 5 * 365 + 10
BACKTEST_MIN_DAILY_BARS = 252
BACKTEST_IDEAL_DAILY_BARS = 756
_YF_HISTORY_CACHE: dict[str, pd.DataFrame] = {}


def _candles_to_frame(candles: list[DailyCandle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    return pd.DataFrame([{
        "date": c.date,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
        "adj_close": c.adj_close,
    } for c in candles])


def _delivery_rows_to_frame(rows: list[DeliveryData]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "date": row.date,
        "delivery_pct": row.delivery_pct,
        "delivery_volume": row.delivery_volume,
        "traded_volume": row.traded_volume,
    } for row in rows]).sort_values("date").reset_index(drop=True)


def _fetch_yf_history(symbol: str, years: int = 5) -> pd.DataFrame:
    cache_key = f"{symbol}|{years}"
    if cache_key in _YF_HISTORY_CACHE:
        return _YF_HISTORY_CACHE[cache_key].copy()

    try:
        data = yf.download(
            symbol,
            period=f"{years}y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if data.empty:
            frame = pd.DataFrame()
        else:
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(-1)
            frame = data.reset_index()
            frame.columns = [str(col).lower().replace(" ", "_") for col in frame.columns]
            if "date" not in frame.columns and "datetime" in frame.columns:
                frame = frame.rename(columns={"datetime": "date"})
            keep = [col for col in ("date", "open", "high", "low", "close", "volume") if col in frame.columns]
            frame = frame[keep].dropna(subset=["close"]).reset_index(drop=True)
        _YF_HISTORY_CACHE[cache_key] = frame.copy()
        return frame
    except Exception as exc:
        logger.warning("Failed to fetch index history for %s: %s", symbol, exc)
        _YF_HISTORY_CACHE[cache_key] = pd.DataFrame()
        return pd.DataFrame()


def _peer_candle_frames(
    db: Session,
    symbol: str,
    sector: str,
    target_turnover_cr: float,
    target_atr_pct: float,
) -> list[dict]:
    if not sector or sector == "DATA NOT PROVIDED":
        return []

    start_date = datetime.now().date() - timedelta(days=BACKTEST_LOOKBACK_DAYS)
    peers = db.query(Stock).filter(
        Stock.sector == sector,
        Stock.symbol != symbol,
        Stock.is_active == True,  # noqa: E712
    ).limit(40).all()
    peer_symbols = [peer.symbol for peer in peers if peer.symbol]
    if not peer_symbols:
        return []

    rows = db.query(DailyCandle).filter(
        DailyCandle.symbol.in_(peer_symbols),
        DailyCandle.date >= start_date,
    ).order_by(DailyCandle.symbol, DailyCandle.date).all()

    grouped: dict[str, list[DailyCandle]] = {}
    for row in rows:
        grouped.setdefault(row.symbol, []).append(row)

    frames = []
    for peer_symbol, peer_rows in grouped.items():
        if len(peer_rows) < BACKTEST_MIN_DAILY_BARS:
            continue
        frame = _candles_to_frame(peer_rows)
        closes = frame["close"].astype(float)
        highs = frame["high"].astype(float)
        lows = frame["low"].astype(float)
        volumes = frame["volume"].astype(float)
        avg_turnover = float((closes.tail(20) * volumes.tail(20)).mean() / 1e7) if len(frame) >= 20 else 0
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs(),
        ], axis=1).max(axis=1)
        peer_atr_pct = float(tr.tail(14).mean() / closes.iloc[-1] * 100) if len(frame) >= 20 and closes.iloc[-1] else 0
        liquidity_ok = target_turnover_cr <= 0 or 0.35 * target_turnover_cr <= avg_turnover <= 3.0 * target_turnover_cr
        volatility_ok = target_atr_pct <= 0 or abs(peer_atr_pct - target_atr_pct) <= max(2.5, target_atr_pct * 0.75)
        if liquidity_ok and volatility_ok:
            frames.append({
                "symbol": peer_symbol,
                "frame": frame,
                "avg_turnover_cr": round(avg_turnover, 2),
                "atr_pct": round(peer_atr_pct, 2),
            })
        if len(frames) >= 12:
            break
    return frames


def _extract_raw_data(db: Session, symbol: str, mode: str = "full") -> dict | None:
    """
    Extract all raw technical data needed by the scoring models.
    Returns a comprehensive dict of metrics, or None if insufficient data.
    """
    lookback_days = 400 if mode == "batch" else 5 * 365 + 10

    # Get daily candles. Full mode uses deep history for the Backtest Engine.
    candles = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol,
        DailyCandle.date >= datetime.now().date() - timedelta(days=lookback_days),
    ).order_by(DailyCandle.date).all()

    min_candles = 10 if mode == "batch" else 50
    if not candles or len(candles) < min_candles:
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

    if mode == "batch":
        backtest_inputs = {
            "delivery_df": pd.DataFrame(),
            "peer_candles": [],
            "nifty_df": pd.DataFrame(),
            "sector_index_df": pd.DataFrame(),
            "backtest_data_notes": ["Backtest side data skipped in batch mode"],
        }
    else:
        start_date = datetime.now().date() - timedelta(days=BACKTEST_LOOKBACK_DAYS)
        delivery_rows = db.query(DeliveryData).filter(
            DeliveryData.symbol == symbol,
            DeliveryData.date >= start_date,
        ).order_by(DeliveryData.date).all()
        sector_index_symbol = market_context.get("sector_index_symbol") or "^NSEI"
        backtest_inputs = {
            "delivery_df": _delivery_rows_to_frame(delivery_rows),
            "peer_candles": _peer_candle_frames(
                db,
                symbol=symbol,
                sector=market_context.get("sector") or "",
                target_turnover_cr=avg_turnover_cr,
                target_atr_pct=atr_pct,
            ),
            "nifty_df": _fetch_yf_history("^NSEI", years=5),
            "sector_index_df": _fetch_yf_history(sector_index_symbol, years=5),
            "backtest_data_notes": [],
        }

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
        "weekly_df": (
            df.set_index(pd.to_datetime(df["date"]))[["open", "high", "low", "close", "volume"]]
            .resample("W-FRI")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
            .reset_index()
            .rename(columns={"index": "date"})
        ),
        "monthly_df": (
            df.set_index(pd.to_datetime(df["date"]))[["open", "high", "low", "close", "volume"]]
            .resample("ME")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
            .reset_index()
            .rename(columns={"index": "date"})
        ),
        **backtest_inputs,
    }


ACTIVE_ENGINE_KEYS = (
    "TITAN",
    "TITAN_v19",
    "Swing_AI",
    "Swing_AI_Hyper",
    "KING",
    "JP_Pattern",
)

MODEL_WEIGHTS = {key: 1 / len(ACTIVE_ENGINE_KEYS) for key in ACTIVE_ENGINE_KEYS}


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
def _safe_pct(new_value: float | None, old_value: float | None) -> float:
    if old_value in (None, 0) or new_value is None:
        return 0.0
    return float((new_value / old_value - 1) * 100)


def _frame_indicator_snapshot(frame: pd.DataFrame) -> dict:
    if frame is None or frame.empty or len(frame) < 3:
        return {
            "rsi": None,
            "rsi_prev": None,
            "ema_6": None,
            "ema_10": None,
            "ema_20": None,
            "macd_bull_cross": False,
            "macd_histogram": None,
            "vol_ratio": 0,
            "range_pct": 0,
        }

    closes = frame["close"].astype(float).reset_index(drop=True)
    highs = frame["high"].astype(float).reset_index(drop=True)
    lows = frame["low"].astype(float).reset_index(drop=True)
    volumes = frame["volume"].astype(float).reset_index(drop=True)
    rsi_series = _calc_rsi(closes, 14)
    macd = _calc_macd(closes, 12, 26, 9)
    ema_6 = _calc_ema(closes, 6)
    ema_10 = _calc_ema(closes, 10)
    ema_20 = _calc_ema(closes, 20)
    lookback = min(40, len(frame))
    recent_high = float(highs.tail(lookback).max()) if lookback else 0
    recent_low = float(lows.tail(lookback).min()) if lookback else 0
    vol_window = min(20, len(frame))
    avg_volume = float(volumes.tail(vol_window).mean()) if vol_window else 0
    vol_ratio = float(volumes.iloc[-1] / avg_volume) if avg_volume > 0 else 0
    hist = macd["histogram"]
    macd_bull_cross = (
        len(hist) >= 2
        and not pd.isna(hist.iloc[-1])
        and not pd.isna(hist.iloc[-2])
        and float(hist.iloc[-1]) > 0
        and float(hist.iloc[-2]) <= 0
    )

    return {
        "rsi": None if pd.isna(rsi_series.iloc[-1]) else float(rsi_series.iloc[-1]),
        "rsi_prev": None if len(rsi_series) < 2 or pd.isna(rsi_series.iloc[-2]) else float(rsi_series.iloc[-2]),
        "ema_6": None if pd.isna(ema_6.iloc[-1]) else float(ema_6.iloc[-1]),
        "ema_10": None if pd.isna(ema_10.iloc[-1]) else float(ema_10.iloc[-1]),
        "ema_20": None if pd.isna(ema_20.iloc[-1]) else float(ema_20.iloc[-1]),
        "macd_bull_cross": macd_bull_cross,
        "macd_histogram": None if pd.isna(hist.iloc[-1]) else float(hist.iloc[-1]),
        "vol_ratio": round(vol_ratio, 2),
        "range_pct": round((recent_high - recent_low) / recent_low * 100, 2) if recent_low > 0 else 0,
    }


def _add_pattern_signal(signals: list[dict], pattern: str, timeframe: str, score: float, evidence: str) -> None:
    signals.append({
        "pattern": pattern,
        "timeframe": timeframe,
        "score": round(float(score), 1),
        "evidence": evidence,
    })


def _detect_jp_pattern_signals(frame: pd.DataFrame, timeframe: str) -> list[dict]:
    """Mechanical detector for the Top 12 JP swing patterns from the document."""
    if frame is None or frame.empty or len(frame) < 12:
        return []

    frame = frame.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    closes = frame["close"].astype(float)
    highs = frame["high"].astype(float)
    lows = frame["low"].astype(float)
    opens = frame["open"].astype(float)
    volumes = frame["volume"].astype(float)
    n = len(frame)
    latest_close = float(closes.iloc[-1])
    latest_open = float(opens.iloc[-1])
    snapshot = _frame_indicator_snapshot(frame)
    signals: list[dict] = []

    lookback = min(40, n)
    recent_high = float(highs.tail(lookback).max())
    recent_low = float(lows.tail(lookback).min())
    range_pct = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0
    recent_low_window = float(lows.tail(min(12, n)).min())
    recent_range = (
        (float(highs.tail(min(12, n)).max()) - recent_low_window) / recent_low_window * 100
        if recent_low_window > 0 else 0
    )
    prior_slice = slice(max(0, n - 32), max(1, n - 12))
    prior_low = float(lows.iloc[prior_slice].min()) if n >= 24 else 0
    prior_range = (
        (float(highs.iloc[prior_slice].max()) - prior_low) / prior_low * 100
        if n >= 24 and prior_low > 0 else range_pct
    )
    avg_volume_20 = float(volumes.tail(min(20, n)).mean()) if n else 0
    volume_contracting = n >= 24 and float(volumes.tail(10).mean()) < float(volumes.iloc[-24:-10].mean()) * 0.9
    volume_expanding = avg_volume_20 > 0 and float(volumes.iloc[-1]) >= avg_volume_20 * 1.2

    if n >= 30 and range_pct <= 18 and recent_range <= max(10, prior_range * 0.75):
        score = 9 if volume_contracting else 7
        _add_pattern_signal(signals, "Weekly VCP" if timeframe == "weekly" else "VCP", timeframe, score, "Volatility contraction with tightening recent range")

    if n >= 30:
        left_high = float(highs.iloc[-30:-22].max())
        mid_low = float(lows.iloc[-22:-8].min())
        right_high = float(highs.iloc[-8:].max())
        handle_low = float(lows.tail(8).min())
        handle_range = (float(highs.tail(8).max()) - handle_low) / handle_low * 100 if handle_low > 0 else 0
        if mid_low <= left_high * 0.9 and right_high >= left_high * 0.94 and handle_range <= 10:
            _add_pattern_signal(signals, "Cup & Handle", timeframe, 8.5, "Rounded base with right-side recovery and tight handle")

    if n >= 24 and range_pct <= 14 and latest_close >= recent_high * 0.96:
        _add_pattern_signal(signals, "Flat Base Breakout", timeframe, 8 if volume_expanding else 6.5, "Tight base near breakout level")

    if n >= 32:
        first_low = float(lows.iloc[-32:-18].min())
        second_low = float(lows.iloc[-18:-5].min())
        neckline = float(highs.iloc[-26:-5].max())
        lows_match = abs(first_low - second_low) / max(first_low, second_low) <= 0.045
        if lows_match and latest_close >= neckline * 0.96:
            _add_pattern_signal(signals, "Double Bottom", timeframe, 8, "Two similar lows with neckline reclaim attempt")

    if n >= 36:
        left_shoulder = float(lows.iloc[-36:-26].min())
        head = float(lows.iloc[-26:-14].min())
        right_shoulder = float(lows.iloc[-14:-4].min())
        neckline = float(highs.iloc[-30:-4].max())
        shoulders_match = abs(left_shoulder - right_shoulder) / max(left_shoulder, right_shoulder) <= 0.08
        if head < left_shoulder * 0.97 and head < right_shoulder * 0.97 and shoulders_match and latest_close >= neckline * 0.95:
            _add_pattern_signal(signals, "Inverse Head & Shoulders", timeframe, 8, "Head lower than both shoulders with neckline pressure")

    if n >= 24:
        high_slope = float(highs.tail(8).mean() - highs.iloc[-24:-16].mean())
        low_slope = float(lows.tail(8).mean() - lows.iloc[-24:-16].mean())
        narrowing = recent_range < prior_range * 0.8 if prior_range else False
        if high_slope < 0 and low_slope < 0 and narrowing and latest_close > float(highs.tail(8).max()) * 0.98:
            _add_pattern_signal(signals, "Falling Wedge Breakout", timeframe, 8.5, "Downward narrowing structure with reclaim near wedge top")

    if n >= 24:
        impulse = _safe_pct(float(closes.iloc[-12]), float(closes.iloc[-24]))
        pullback_depth = _safe_pct(float(lows.tail(10).min()), float(highs.iloc[-14:-8].max()))
        if impulse >= 10 and pullback_depth >= -12 and recent_range <= 12:
            _add_pattern_signal(signals, "Bull Flag", timeframe, 7.5, "Impulse move followed by shallow tight consolidation")

    if n >= 30:
        base_support = float(lows.iloc[-30:-6].min())
        spring_low = float(lows.tail(6).min())
        sos_high = float(highs.iloc[-30:-6].max())
        if spring_low < base_support * 0.985 and latest_close > base_support and latest_close >= sos_high * 0.94:
            _add_pattern_signal(signals, "Wyckoff Spring to SOS", timeframe, 9, "Support undercut reclaimed with strength near SOS zone")

    ema_20 = snapshot["ema_20"]
    if ema_20 and latest_close > ema_20 and float(lows.iloc[-1]) <= ema_20 * 1.02 and latest_close > latest_open:
        label = "Weekly 20 MA Touch & Bounce" if timeframe == "weekly" else "20 MA Touch & Bounce"
        _add_pattern_signal(signals, label, timeframe, 7.5, "Price touched the 20-period average and closed back above it")

    if n >= 24:
        sweep_support = float(lows.iloc[-24:-4].min())
        sweep_low = float(lows.tail(4).min())
        if sweep_low < sweep_support * 0.99 and latest_close > sweep_support:
            _add_pattern_signal(signals, "Liquidity Sweep + Reclaim", timeframe, 8.5, "Recent low swept prior support and reclaimed it")

    if snapshot["macd_bull_cross"] and (range_pct <= 18 or recent_range <= 10):
        _add_pattern_signal(signals, "MACD Fresh Bullish Crossover After Compression", timeframe, 8, "MACD histogram crossed positive after a compressed range")

    rsi = snapshot["rsi"]
    rsi_prev = snapshot["rsi_prev"]
    if rsi is not None and rsi_prev is not None and rsi >= 60 and rsi_prev < 60 and volume_expanding:
        _add_pattern_signal(signals, "RSI 60 Reclaim + Volume Expansion", timeframe, 8.5, "RSI reclaimed 60 with expanding live volume")

    signals.sort(key=lambda item: item["score"], reverse=True)
    return signals[:6]


def _score_jp_pattern_engine(data: dict) -> dict:
    """
    JP Pattern Engine v1.
    Implements the sixth-engine document: monthly primary trend, weekly pattern
    selection, daily entry confirmation, and the Top 12 JP momentum swing patterns.
    """
    daily_df = data.get("candles_df") if isinstance(data.get("candles_df"), pd.DataFrame) else pd.DataFrame()
    weekly_df = data.get("weekly_df") if isinstance(data.get("weekly_df"), pd.DataFrame) else pd.DataFrame()
    monthly_df = data.get("monthly_df") if isinstance(data.get("monthly_df"), pd.DataFrame) else pd.DataFrame()

    daily_signals = _detect_jp_pattern_signals(daily_df, "daily")
    weekly_signals = _detect_jp_pattern_signals(weekly_df, "weekly")
    monthly_signals = _detect_jp_pattern_signals(monthly_df, "monthly")
    all_signals = monthly_signals + weekly_signals + daily_signals
    top_patterns = sorted(all_signals, key=lambda item: item["score"], reverse=True)[:8]
    active_timeframes = {signal["timeframe"] for signal in all_signals}

    monthly_snapshot = _frame_indicator_snapshot(monthly_df)
    weekly_snapshot = _frame_indicator_snapshot(weekly_df)
    daily_snapshot = _frame_indicator_snapshot(daily_df)
    cmp = data["cmp"]

    m1 = 0
    if monthly_df is not None and len(monthly_df) >= 6:
        monthly_close = float(monthly_df["close"].astype(float).iloc[-1])
        if monthly_snapshot["ema_6"] and monthly_close > monthly_snapshot["ema_6"]:
            m1 += 4
        if monthly_snapshot["ema_10"] and monthly_close > monthly_snapshot["ema_10"]:
            m1 += 3
        if len(monthly_df) >= 4 and monthly_close > float(monthly_df["close"].astype(float).iloc[-4]):
            m1 += 3
        if monthly_snapshot["rsi"] and monthly_snapshot["rsi"] >= 55:
            m1 += 3
        if monthly_signals:
            m1 += 2
    elif data["weekly_bias"] == "bullish":
        m1 = 7
    m1 = min(15, m1)

    w1 = 0
    if weekly_signals:
        w1 += min(16, weekly_signals[0]["score"] * 1.8)
        if len(weekly_signals) >= 2:
            w1 += 3
    if data["weekly_structure"] == "HH/HL":
        w1 += 3
    elif data["weekly_structure"] == "range":
        w1 += 1.5
    if weekly_snapshot["rsi"] and weekly_snapshot["rsi"] >= 60:
        w1 += 3
    w1 = min(25, w1)

    d1 = 0
    if daily_signals:
        d1 += min(10, daily_signals[0]["score"] * 1.15)
    if data["macd_crossover"] or daily_snapshot["macd_bull_cross"]:
        d1 += 3
    if data["rsi"] and data["rsi"] >= 60:
        d1 += 3
    elif data["rsi"] and data["rsi"] >= 55:
        d1 += 2
    if data["close_position"] >= 0.65:
        d1 += 2
    if data["trigger"] and cmp >= data["trigger"] * 0.97:
        d1 += 2
    d1 = min(20, d1)

    unique_patterns = {signal["pattern"] for signal in all_signals}
    p1 = min(8, len(unique_patterns) * 1.6)
    if "weekly" in active_timeframes:
        p1 += 3
    if "daily" in active_timeframes:
        p1 += 2
    if "monthly" in active_timeframes:
        p1 += 2
    p1 = min(15, p1)

    v1 = 0
    if data["vol_ratio"] >= 1.8:
        v1 += 5
    elif data["vol_ratio"] >= 1.2:
        v1 += 3
    if data["base_contraction"]:
        v1 += 3
    if data["delivery_trend"] in ("improving", "strong") or data["delivery_proxy"] == "supportive":
        v1 += 3
    if data["value_10d_cr"] >= 50:
        v1 += 2
    elif data["value_10d_cr"] >= 10:
        v1 += 1
    if data["volatility_state"] in ("contracting", "expanding"):
        v1 += 2
    v1 = min(15, v1)

    r1 = 0
    if 0 < data["sl_pct"] <= 4:
        r1 += 3
    elif data["sl_pct"] <= 6:
        r1 += 2
    elif data["sl_pct"] <= 8:
        r1 += 1
    if data["overhead_supply"] == "open_air":
        r1 += 3
    elif data["overhead_supply"] == "light":
        r1 += 2
    elif data["overhead_supply"] == "moderate":
        r1 += 1
    if len(daily_df) >= 180:
        r1 += 2
    elif len(daily_df) >= 60:
        r1 += 1
    if len(weekly_df) >= 30:
        r1 += 1
    if len(monthly_df) >= 12:
        r1 += 1
    r1 = min(10, r1)

    raw_score = round(m1 + w1 + d1 + p1 + v1 + r1, 1)
    penalties = 0
    penalty_reasons = []
    if not top_patterns:
        penalties += 10
        penalty_reasons.append("No JP Top-12 pattern confirmed on live candles")
    if data["extension_pct"] > 5:
        penalties += 8
        penalty_reasons.append("CMP more than 5% above trigger")
    elif data["extension_pct"] > 3:
        penalties += 4
        penalty_reasons.append("CMP more than 3% above trigger")
    if data["sl_pct"] > 9:
        penalties += 8
        penalty_reasons.append("Stop loss too wide for 10-25% swing target")
    elif data["sl_pct"] > 7:
        penalties += 4
        penalty_reasons.append("Stop loss is wider than ideal")
    if data["overhead_supply"] == "heavy":
        penalties += 5
        penalty_reasons.append("Heavy overhead supply blocks clean swing runway")
    if data["weak_close"] or data["upper_wick_heavy"]:
        penalties += 4
        penalty_reasons.append("Weak close or heavy upper wick on latest candle")
    if data["weekly_bias"] == "bearish" and not weekly_signals:
        penalties += 6
        penalty_reasons.append("Weekly structure is bearish without a reversal pattern")

    final_score = round(max(0, raw_score - penalties), 1)
    probability = round(_clamp(final_score + (3 if len(active_timeframes) >= 2 else 0), 25, 92), 1)

    if penalties >= 18 or final_score < 45:
        verdict = "AVOID"
    elif final_score >= 82 and data["sl_pct"] <= 6 and data["extension_pct"] <= 3:
        verdict = "STRONG BUY"
    elif final_score >= 72 and data["sl_pct"] <= 7:
        verdict = "BUY"
    elif final_score >= 62:
        verdict = "HOLD"
    elif final_score >= 52:
        verdict = "WAIT"
    else:
        verdict = "AVOID"

    return {
        "model": "JP Pattern Engine v1",
        "scanner_score": final_score,
        "selection_total": final_score,
        "scanner_raw": raw_score,
        "penalties": penalties,
        "penalty_reasons": penalty_reasons,
        "components": {
            "M1_monthly_primary_trend": {"score": round(m1, 1), "max": 15},
            "W1_weekly_pattern_quality": {"score": round(w1, 1), "max": 25},
            "D1_daily_entry_confirmation": {"score": round(d1, 1), "max": 20},
            "P1_top_12_pattern_breadth": {"score": round(p1, 1), "max": 15},
            "V1_volume_delivery": {"score": round(v1, 1), "max": 15},
            "R1_risk_runway_data": {"score": round(r1, 1), "max": 10},
        },
        "top_patterns": top_patterns,
        "pattern_stack": {
            "monthly": monthly_signals,
            "weekly": weekly_signals,
            "daily": daily_signals,
        },
        "timeframe_roles": {
            "monthly": "primary trend and multibagger structure",
            "weekly": "main stock-selection and pattern-detection chart",
            "daily": "precise entry, stop loss and breakout confirmation",
        },
        "monthly_rsi": round(monthly_snapshot["rsi"], 2) if monthly_snapshot["rsi"] is not None else None,
        "weekly_rsi": round(weekly_snapshot["rsi"], 2) if weekly_snapshot["rsi"] is not None else None,
        "daily_rsi": round(daily_snapshot["rsi"], 2) if daily_snapshot["rsi"] is not None else None,
        "probability_pct": probability,
        "final_probability": probability,
        "verdict": verdict,
        "selection_grade": "A+" if final_score >= 85 else "A" if final_score >= 72 else "B" if final_score >= 58 else "SKIP",
        "selection_action": "TRADE" if verdict in ("BUY", "STRONG BUY") else "WAIT RETEST" if verdict in ("HOLD", "WAIT") else "SKIP",
    }


def _backtest_setup_family(pattern_name: str | None) -> str:
    if pattern_name in ("pullback_ema20", "bull_flag"):
        return "Pullback Continuation"
    if pattern_name in ("base_breakout", "vcp", "box", "ascending_triangle", "cup_handle"):
        return "Clean Base Breakout"
    if pattern_name in ("trendline_break", "rounding_bottom"):
        return "Breakout Retest"
    return "Clean Base Breakout"


def _backtest_rsi_zone(rsi: float | None) -> str:
    rsi = rsi or 50
    if rsi >= 65:
        return "strong"
    if rsi >= 55:
        return "constructive"
    if rsi >= 45:
        return "neutral"
    return "weak"


def _backtest_ema_structure(row: pd.Series) -> str:
    ema20 = row.get("ema20")
    ema50 = row.get("ema50")
    ema200 = row.get("ema200")
    close = row.get("close")
    if pd.notna(ema20) and pd.notna(ema50) and pd.notna(ema200) and ema20 > ema50 > ema200 and close > ema20:
        return "bullish"
    if pd.notna(ema20) and pd.notna(ema50) and ema20 > ema50 and close > ema20:
        return "constructive"
    if pd.notna(ema20) and close >= ema20:
        return "mixed"
    return "weak"


def _backtest_volume_condition(vol_ratio: float | None) -> str:
    vol_ratio = vol_ratio or 0
    if vol_ratio >= 1.5:
        return "surge"
    if vol_ratio >= 1.0:
        return "normal"
    return "weak"


def _backtest_prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["date_dt"] = frame["date"]
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = frame[col].astype(float)

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    frame["ema20"] = close.ewm(span=20, adjust=False).mean()
    frame["ema50"] = close.ewm(span=50, adjust=False).mean()
    frame["ema100"] = close.ewm(span=100, adjust=False).mean()
    frame["ema200"] = close.ewm(span=200, adjust=False).mean()
    frame["rsi14"] = _calc_rsi(close, 14)
    macd = _calc_macd(close, 12, 26, 9)
    frame["macd"] = macd["macd"]
    frame["macd_signal"] = macd["signal"]
    bb = _calc_bollinger(close, 20, 2)
    frame["bb_middle"] = bb["middle"]
    frame["bb_upper"] = bb["upper"]
    frame["bb_lower"] = bb["lower"]
    frame["atr14"] = true_range.rolling(14, min_periods=5).mean()
    frame["avg_vol20"] = volume.rolling(20, min_periods=5).mean()
    frame["vol_ratio"] = volume / frame["avg_vol20"].replace(0, np.nan)
    frame["high_52w"] = high.rolling(252, min_periods=60).max()
    frame["pct_from_52w"] = (close / frame["high_52w"] - 1) * 100
    return frame


def _condition_from_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "close" not in frame:
        return pd.DataFrame(columns=["date_dt", "condition"])
    out = frame.copy().reset_index(drop=True)
    out["date_dt"] = pd.to_datetime(out["date"])
    close = out["close"].astype(float)
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema200"] = close.ewm(span=200, adjust=False).mean()
    out["perf20"] = close.pct_change(20) * 100
    out["condition"] = "neutral"
    out.loc[(close > out["ema50"]) & (out["perf20"] > 0), "condition"] = "bullish"
    out.loc[(close < out["ema50"]) & (out["perf20"] < 0), "condition"] = "bearish"
    out.loc[(pd.notna(out["ema200"])) & (close > out["ema50"]) & (out["ema50"] > out["ema200"]), "condition"] = "bullish"
    out.loc[(pd.notna(out["ema200"])) & (close < out["ema50"]) & (out["ema50"] < out["ema200"]), "condition"] = "bearish"
    return out[["date_dt", "condition"]].dropna().sort_values("date_dt")


def _delivery_condition_frame(delivery_df: pd.DataFrame) -> pd.DataFrame:
    if delivery_df is None or delivery_df.empty or "delivery_pct" not in delivery_df:
        return pd.DataFrame(columns=["date_dt", "delivery_condition"])
    out = delivery_df.copy().reset_index(drop=True)
    out["date_dt"] = pd.to_datetime(out["date"])
    delivery = out["delivery_pct"].astype(float)
    rolling = delivery.rolling(10, min_periods=3).mean()
    out["delivery_condition"] = "neutral"
    out.loc[rolling >= 50, "delivery_condition"] = "supportive"
    out.loc[rolling <= 30, "delivery_condition"] = "weak"
    trend = rolling.diff(5)
    out.loc[(rolling >= 40) & (trend > 2), "delivery_condition"] = "supportive"
    out.loc[(rolling <= 40) & (trend < -2), "delivery_condition"] = "weak"
    return out[["date_dt", "delivery_condition"]].dropna().sort_values("date_dt")


def _weekly_condition_frame(weekly_df: pd.DataFrame) -> pd.DataFrame:
    if weekly_df is None or weekly_df.empty or "close" not in weekly_df:
        return pd.DataFrame(columns=["date_dt", "weekly_condition"])
    out = weekly_df.copy().reset_index(drop=True)
    out["date_dt"] = pd.to_datetime(out["date"])
    close = out["close"].astype(float)
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema30 = close.ewm(span=30, adjust=False).mean()
    out["weekly_condition"] = "neutral"
    out.loc[(close > ema10) & (ema10 > ema30), "weekly_condition"] = "bullish"
    out.loc[(close < ema10) & (ema10 < ema30), "weekly_condition"] = "bearish"
    return out[["date_dt", "weekly_condition"]].dropna().sort_values("date_dt")


def _merge_asof_condition(base: pd.DataFrame, side: pd.DataFrame, column: str) -> pd.DataFrame:
    if side is None or side.empty or column not in side:
        base[column] = "unknown"
        return base
    merged = pd.merge_asof(
        base.sort_values("date_dt"),
        side[["date_dt", column]].sort_values("date_dt"),
        on="date_dt",
        direction="backward",
    )
    merged[column] = merged[column].fillna("unknown")
    return merged.sort_index()


def _backtest_enrich_context(
    frame: pd.DataFrame,
    delivery_df: pd.DataFrame | None,
    nifty_df: pd.DataFrame | None,
    sector_index_df: pd.DataFrame | None,
    weekly_df: pd.DataFrame | None,
) -> pd.DataFrame:
    enriched = frame.copy().sort_values("date_dt").reset_index(drop=True)
    enriched = _merge_asof_condition(enriched, _delivery_condition_frame(delivery_df), "delivery_condition")
    nifty_conditions = _condition_from_price_frame(nifty_df).rename(columns={"condition": "market_condition"})
    sector_conditions = _condition_from_price_frame(sector_index_df).rename(columns={"condition": "sector_condition"})
    enriched = _merge_asof_condition(enriched, nifty_conditions, "market_condition")
    enriched = _merge_asof_condition(enriched, sector_conditions, "sector_condition")
    enriched = _merge_asof_condition(enriched, _weekly_condition_frame(weekly_df), "weekly_condition")
    return enriched.reset_index(drop=True)


def _backtest_signal_family(frame: pd.DataFrame, idx: int) -> str | None:
    row = frame.iloc[idx]
    prior_20 = frame.iloc[idx - 20:idx]
    if prior_20.empty:
        return None

    close = row["close"]
    open_ = row["open"]
    high = row["high"]
    low = row["low"]
    ema20 = row.get("ema20")
    ema50 = row.get("ema50")
    vol_ratio = row.get("vol_ratio") or 0
    prior_high = prior_20["high"].max()
    candle_range = max(high - low, 0.01)
    close_quality = (close - low) / candle_range

    if close > prior_high and vol_ratio >= 1.0 and close_quality >= 0.55 and close <= prior_high * 1.05:
        return "Clean Base Breakout"

    if (
        pd.notna(ema20)
        and pd.notna(ema50)
        and close > ema20 >= ema50
        and low <= ema20 * 1.02
        and close > open_
        and vol_ratio >= 0.8
    ):
        return "Pullback Continuation"

    breakout_levels = []
    for lookback_idx in range(max(20, idx - 15), idx):
        previous = frame.iloc[lookback_idx - 20:lookback_idx]
        if previous.empty:
            continue
        level = previous["high"].max()
        if frame.iloc[lookback_idx]["close"] > level:
            breakout_levels.append(level)

    if breakout_levels:
        level = breakout_levels[-1]
        if low <= level * 1.02 and close >= level * 0.99 and close > open_:
            return "Breakout Retest"

    return None


def _backtest_current_features(data: dict) -> dict:
    current_row = pd.Series({
        "close": data.get("cmp"),
        "ema20": data.get("ema_20"),
        "ema50": data.get("ema_50"),
        "ema200": data.get("ema_200"),
    })
    return {
        "family": _backtest_setup_family(data.get("pattern_name")),
        "rsi_zone": _backtest_rsi_zone(data.get("rsi")),
        "ema_structure": _backtest_ema_structure(current_row),
        "volume_condition": _backtest_volume_condition(data.get("vol_ratio")),
        "delivery_condition": (
            "supportive" if data.get("delivery_trend") == "rising" or data.get("delivery_proxy") == "supportive"
            else "weak" if data.get("delivery_trend") == "falling" or data.get("delivery_proxy") == "weak"
            else "neutral"
        ),
        "market_condition": (
            "bullish" if data.get("nifty_trend_state") in ("bullish", "uptrend", "positive")
            else "bearish" if data.get("nifty_trend_state") in ("bearish", "downtrend", "negative")
            else "neutral"
        ),
        "sector_condition": (
            "bullish" if data.get("sector_structure") == "HH/HL" or (data.get("sector_momentum_score") or 0) >= 6
            else "bearish" if data.get("sector_structure") == "LH/LL" or (data.get("sector_momentum_score") or 0) <= 2
            else "neutral"
        ),
        "weekly_condition": (
            "bullish" if data.get("weekly_bias") == "bullish" or data.get("weekly_structure") == "HH/HL"
            else "bearish" if data.get("weekly_bias") == "bearish" or data.get("weekly_structure") == "LH/LL"
            else "neutral"
        ),
        "atr_pct": data.get("atr_pct") or 0,
        "pct_from_52w": data.get("pct_from_52w") or -100,
    }


def _backtest_is_similar_setup(row: pd.Series, family: str, current: dict) -> bool:
    if family != current["family"]:
        return False
    if _backtest_rsi_zone(row.get("rsi14")) != current["rsi_zone"]:
        return False

    current_structure = current["ema_structure"]
    row_structure = _backtest_ema_structure(row)
    if current_structure in ("bullish", "constructive") and row_structure not in ("bullish", "constructive"):
        return False
    if current_structure in ("mixed", "weak") and row_structure == "weak" and current_structure != "weak":
        return False

    current_volume = current["volume_condition"]
    row_volume = _backtest_volume_condition(row.get("vol_ratio"))
    if current_volume == "surge" and row_volume != "surge":
        return False
    if current_volume == "normal" and row_volume == "weak":
        return False

    row_delivery = row.get("delivery_condition", "unknown")
    if current["delivery_condition"] == "supportive" and row_delivery == "weak":
        return False

    for condition_key in ("market_condition", "sector_condition", "weekly_condition"):
        current_condition = current.get(condition_key)
        row_condition = row.get(condition_key, "unknown")
        if current_condition == "bullish" and row_condition == "bearish":
            return False
        if current_condition == "bearish" and row_condition == "bullish":
            return False

    atr_value = row.get("atr14")
    atr_pct = float(atr_value) / row["close"] * 100 if pd.notna(atr_value) and row["close"] else 0
    current_atr = current["atr_pct"]
    if current_atr and abs(atr_pct - current_atr) > max(2.0, current_atr * 0.7):
        return False

    pct_from_52w = row.get("pct_from_52w")
    if pd.notna(pct_from_52w) and abs(pct_from_52w - current["pct_from_52w"]) > 10:
        return False

    return True


def _backtest_simulate_trade(frame: pd.DataFrame, idx: int, family: str, source: str, source_symbol: str) -> dict | None:
    entry_row = frame.iloc[idx]
    entry = float(entry_row["close"])
    if entry <= 0:
        return None

    prior_20 = frame.iloc[idx - 20:idx]
    prior_10 = frame.iloc[idx - 10:idx]
    trigger = float(prior_20["high"].max()) if not prior_20.empty else entry
    atr_value = entry_row.get("atr14")
    atr = float(atr_value) if pd.notna(atr_value) and atr_value > 0 else entry * 0.025
    support_stop = float(prior_10["low"].min()) if not prior_10.empty else entry - atr
    stop_loss = max(support_stop, entry * 0.95, entry - 1.5 * atr)
    stop_loss = min(stop_loss, entry * 0.995)
    risk_pct = max((entry - stop_loss) / entry * 100, 0.5)

    t1 = entry * 1.08
    t2 = entry * 1.12
    t3 = entry * 1.15
    max_target_hit = 0
    max_drawdown = 0.0
    exit_price = None
    exit_reason = "TIME"
    holding_days = 20
    horizon = frame.iloc[idx + 1:min(len(frame), idx + 21)]
    if len(horizon) < 5:
        return None

    for day_count, (_, row) in enumerate(horizon.iterrows(), start=1):
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        max_drawdown = min(max_drawdown, (low / entry - 1) * 100)

        if low <= stop_loss:
            exit_price = stop_loss
            exit_reason = "SL"
            holding_days = day_count
            break
        if high >= t3:
            max_target_hit = 3
            exit_price = t3
            exit_reason = "T3"
            holding_days = day_count
            break
        if high >= t2:
            max_target_hit = max(max_target_hit, 2)
        elif high >= t1:
            max_target_hit = max(max_target_hit, 1)
        if close < stop_loss:
            exit_price = close
            exit_reason = "FAILURE"
            holding_days = day_count
            break

    if exit_price is None:
        final_row = horizon.iloc[-1]
        if max_target_hit == 2:
            exit_price = t2
            exit_reason = "T2"
        elif max_target_hit == 1:
            exit_price = t1
            exit_reason = "T1"
        else:
            exit_price = float(final_row["close"])
        holding_days = len(horizon)

    net_return_pct = ((exit_price / entry - 1) * 100) - 0.50
    r_multiple = net_return_pct / risk_pct if risk_pct > 0 else 0
    false_breakout = exit_reason in ("SL", "FAILURE") or (max_target_hit == 0 and net_return_pct <= 0)

    return {
        "date": str(entry_row.get("date")),
        "source": source,
        "source_symbol": source_symbol,
        "family": family,
        "entry": round(entry, 2),
        "trigger": round(trigger, 2),
        "stop_loss": round(stop_loss, 2),
        "net_return_pct": net_return_pct,
        "r_multiple": r_multiple,
        "target_hit": max_target_hit,
        "sl_hit": exit_reason == "SL",
        "false_breakout": false_breakout,
        "holding_days": holding_days,
        "max_drawdown_pct": max_drawdown,
        "retest_success": family == "Breakout Retest" and max_target_hit >= 1,
    }


def _backtest_collect_trades(frame: pd.DataFrame, current: dict, source: str, source_symbol: str) -> list[dict]:
    trades = []
    if frame.empty:
        return trades
    min_idx = 252 if len(frame) >= 280 else 80
    for idx in range(min_idx, max(min_idx, len(frame) - 20)):
        family = _backtest_signal_family(frame, idx)
        if not family:
            continue
        row = frame.iloc[idx]
        if not _backtest_is_similar_setup(row, family, current):
            continue
        trade = _backtest_simulate_trade(frame, idx, family, source, source_symbol)
        if trade:
            trades.append(trade)
    return trades


def _score_backtest_engine(data: dict) -> dict:
    """Score the current setup using no-lookahead historical setup validation."""
    raw_df = data.get("candles_df")
    empty_components = {
        "B1_sample_size": {"score": 0, "max": 4},
        "B2_win_rate": {"score": 0, "max": 4},
        "B3_average_r": {"score": 0, "max": 4},
        "B4_false_breakout": {"score": 0, "max": 3},
        "B5_recent_performance": {"score": 0, "max": 3},
        "B6_stability": {"score": 0, "max": 2},
    }
    if raw_df is None or raw_df.empty:
        return {
            "model": "Combined Backtest Validation",
            "scanner_score": 0,
            "backtest_score": 0,
            "quality_grade": "Poor",
            "data_status": "BACKTEST DATA INCOMPLETE",
            "penalties": 20,
            "penalty_reasons": ["Historical OHLCV unavailable"],
            "components": empty_components,
            "probability_pct": 0,
            "verdict": "AVOID",
        }

    data_notes = list(data.get("backtest_data_notes") or [])
    delivery_df = data.get("delivery_df")
    nifty_df = data.get("nifty_df")
    sector_index_df = data.get("sector_index_df")
    weekly_df = data.get("weekly_df")
    peer_candles = data.get("peer_candles") or []

    frame = _backtest_enrich_context(
        _backtest_prepare_frame(raw_df),
        delivery_df=delivery_df,
        nifty_df=nifty_df,
        sector_index_df=sector_index_df,
        weekly_df=weekly_df,
    )
    current = _backtest_current_features(data)
    same_stock_trades = _backtest_collect_trades(frame, current, "same_stock", data.get("symbol") or "")
    peer_trades: list[dict] = []
    for peer in peer_candles:
        peer_raw_frame = peer.get("frame")
        if peer_raw_frame is None or peer_raw_frame.empty:
            continue
        peer_frame = _backtest_enrich_context(
            _backtest_prepare_frame(peer_raw_frame),
            delivery_df=None,
            nifty_df=nifty_df,
            sector_index_df=sector_index_df,
            weekly_df=None,
        )
        peer_trades.extend(_backtest_collect_trades(peer_frame, current, "similar_sector_peer", peer.get("symbol") or ""))

    trades = same_stock_trades + peer_trades

    sample_size = len(trades)
    has_min_stock_data = len(frame) >= BACKTEST_MIN_DAILY_BARS
    has_ideal_stock_data = len(frame) >= BACKTEST_IDEAL_DAILY_BARS
    has_weekly_data = weekly_df is not None and not weekly_df.empty and len(weekly_df) >= 52
    has_delivery_data = delivery_df is not None and not delivery_df.empty
    has_nifty_data = nifty_df is not None and not nifty_df.empty
    has_sector_index_data = sector_index_df is not None and not sector_index_df.empty
    has_peer_data = bool(peer_candles)
    data_incomplete = not (
        has_min_stock_data
        and has_weekly_data
        and has_nifty_data
        and has_sector_index_data
        and has_delivery_data
        and has_peer_data
    )

    if sample_size:
        returns = [trade["net_return_pct"] for trade in trades]
        r_values = [trade["r_multiple"] for trade in trades]
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value <= 0]
        win_rate = len(wins) / sample_size * 100
        t1_rate = len([trade for trade in trades if trade["target_hit"] >= 1]) / sample_size * 100
        t2_rate = len([trade for trade in trades if trade["target_hit"] >= 2]) / sample_size * 100
        t3_rate = len([trade for trade in trades if trade["target_hit"] >= 3]) / sample_size * 100
        sl_rate = len([trade for trade in trades if trade["sl_hit"]]) / sample_size * 100
        false_breakout_rate = len([trade for trade in trades if trade["false_breakout"]]) / sample_size * 100
        retest_trades = [trade for trade in trades if trade["family"] == "Breakout Retest"]
        retest_success_rate = (
            len([trade for trade in retest_trades if trade["retest_success"]]) / len(retest_trades) * 100
            if retest_trades else None
        )
        avg_gain = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        avg_r = float(np.mean(r_values)) if r_values else 0.0
        median_r = float(np.median(r_values)) if r_values else 0.0
        best_trade = max(returns)
        worst_trade = min(returns)
        max_drawdown = min(trade["max_drawdown_pct"] for trade in trades)
        avg_holding = float(np.mean([trade["holding_days"] for trade in trades]))
        expectancy = avg_r
        last_date = pd.to_datetime(frame.iloc[-1]["date"])
        recent_trades = [
            trade for trade in trades
            if (last_date - pd.to_datetime(trade["date"])).days <= 183
        ]
        recent_avg_r = float(np.mean([trade["r_multiple"] for trade in recent_trades])) if recent_trades else None
    else:
        win_rate = t1_rate = t2_rate = t3_rate = sl_rate = false_breakout_rate = 0.0
        retest_success_rate = None
        avg_gain = avg_loss = avg_r = median_r = best_trade = worst_trade = max_drawdown = avg_holding = expectancy = 0.0
        recent_avg_r = None

    sample_points = 4 if sample_size >= 20 else 3 if sample_size >= 10 else 2 if sample_size >= 5 else 0
    win_points = 4 if win_rate >= 60 else 3 if win_rate >= 50 else 1 if win_rate >= 40 else 0
    avg_r_points = 4 if avg_r >= 2.0 else 3 if avg_r >= 1.5 else 2 if avg_r >= 1.0 else 0
    false_breakout_points = 3 if false_breakout_rate < 25 else 2 if false_breakout_rate <= 40 else 0
    if recent_avg_r is None:
        recent_points = 1 if sample_size >= 5 else 0
        recent_label = "mixed"
    elif recent_avg_r > 0.25:
        recent_points = 3
        recent_label = "positive"
    elif recent_avg_r >= -0.25:
        recent_points = 1
        recent_label = "mixed"
    else:
        recent_points = 0
        recent_label = "negative"

    same_stock_count = len(same_stock_trades)
    peer_count = len(peer_trades)
    peer_avg_r = float(np.mean([trade["r_multiple"] for trade in peer_trades])) if peer_trades else None
    same_stock_avg_r = float(np.mean([trade["r_multiple"] for trade in same_stock_trades])) if same_stock_trades else None
    sector_confirmed = (data.get("sector_momentum_score") or 0) >= 3 and data.get("nifty_trend_state") != "bearish"
    stable_across_contexts = (
        same_stock_count >= 3
        and peer_count >= 5
        and (same_stock_avg_r or 0) > 0
        and (peer_avg_r or 0) > 0
        and sector_confirmed
        and false_breakout_rate <= 40
    )
    stability_points = 2 if stable_across_contexts else 0
    backtest_score = sample_points + win_points + avg_r_points + false_breakout_points + recent_points + stability_points
    base_score = round(backtest_score * 5, 1)

    penalties = 0
    penalty_reasons = []
    if not has_min_stock_data:
        penalties += 12
        penalty_reasons.append("BACKTEST DATA INCOMPLETE: less than 1 year of daily OHLCV")
    elif not has_ideal_stock_data:
        penalties += 4
        penalty_reasons.append("Less than ideal 3-year daily OHLCV history")
    if not has_weekly_data:
        penalties += 3
        penalty_reasons.append("Weekly OHLCV validation unavailable")
    if not has_nifty_data:
        penalties += 3
        penalty_reasons.append("NIFTY historical window unavailable")
    if not has_sector_index_data:
        penalties += 2
        penalty_reasons.append("Sector index historical window unavailable")
    if not has_delivery_data:
        penalties += 2
        penalty_reasons.append("Delivery percentage history unavailable")
    if not has_peer_data:
        penalties += 3
        penalty_reasons.append("Same-sector similar peer history unavailable")
    if sample_size < 5:
        penalties += 10
        penalty_reasons.append("Fewer than 5 similar historical trades")

    final_score = round(max(0, base_score - penalties), 1)
    if backtest_score >= 17:
        grade = "Excellent"
    elif backtest_score >= 13:
        grade = "Good"
    elif backtest_score >= 9:
        grade = "Average"
    elif backtest_score >= 5:
        grade = "Weak"
    else:
        grade = "Poor"

    if backtest_score >= 17 and penalties == 0:
        verdict = "STRONG BUY"
    elif backtest_score >= 13 and penalties <= 10:
        verdict = "BUY"
    elif backtest_score >= 9:
        verdict = "HOLD"
    elif backtest_score >= 5:
        verdict = "WAIT"
    else:
        verdict = "AVOID"

    return _to_python({
        "model": "Combined Backtest Validation",
        "scanner_score": final_score,
        "scanner_raw": base_score,
        "backtest_score": backtest_score,
        "quality_grade": grade,
        "data_status": "BACKTEST DATA INCOMPLETE" if data_incomplete else "OK",
        "setup_family": current["family"],
        "sample_size": sample_size,
        "same_stock_sample_size": same_stock_count,
        "peer_sample_size": peer_count,
        "data_quality": {
            "daily_bars": len(frame),
            "min_1y_daily_ohlcv": has_min_stock_data,
            "ideal_3y_daily_ohlcv": has_ideal_stock_data,
            "weekly_ohlcv": has_weekly_data,
            "delivery_history": has_delivery_data,
            "nifty_history": has_nifty_data,
            "sector_index_history": has_sector_index_data,
            "similar_sector_peers": len(peer_candles),
            "adjusted_history_source": "yfinance auto_adjust=True where fetched",
            "notes": data_notes,
        },
        "metrics": {
            "number_of_historical_signals": sample_size,
            "same_stock_signals": same_stock_count,
            "similar_peer_signals": peer_count,
            "win_rate": round(win_rate, 1),
            "t1_hit_rate": round(t1_rate, 1),
            "t2_hit_rate": round(t2_rate, 1),
            "t3_hit_rate": round(t3_rate, 1),
            "sl_hit_rate": round(sl_rate, 1),
            "average_gain": round(avg_gain, 2),
            "average_loss": round(avg_loss, 2),
            "average_r_multiple": round(avg_r, 2),
            "median_r_multiple": round(median_r, 2),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            "maximum_drawdown_during_trade": round(max_drawdown, 2),
            "average_holding_period": round(avg_holding, 1),
            "false_breakout_rate": round(false_breakout_rate, 1),
            "retest_success_rate": round(retest_success_rate, 1) if retest_success_rate is not None else None,
            "expectancy_per_trade": round(expectancy, 2),
            "same_stock_average_r": round(same_stock_avg_r, 2) if same_stock_avg_r is not None else None,
            "peer_average_r": round(peer_avg_r, 2) if peer_avg_r is not None else None,
            "stable_across_stock_sector_market": stable_across_contexts,
            "recent_6m_performance": recent_label,
        },
        "components": {
            "B1_sample_size": {"score": sample_points, "max": 4},
            "B2_win_rate": {"score": win_points, "max": 4},
            "B3_average_r": {"score": avg_r_points, "max": 4},
            "B4_false_breakout": {"score": false_breakout_points, "max": 3},
            "B5_recent_performance": {"score": recent_points, "max": 3},
            "B6_stability": {"score": stability_points, "max": 2},
        },
        "penalties": penalties,
        "penalty_reasons": penalty_reasons,
        "probability_pct": final_score,
        "verdict": verdict,
    })


def _backtest_not_run_result(reason: str = "Backtest skipped for batch speed") -> dict:
    return {
        "model": "Combined Backtest Validation",
        "scanner_score": None,
        "backtest_score": None,
        "quality_grade": "Not run",
        "data_status": "BACKTEST NOT RUN",
        "setup_family": None,
        "sample_size": None,
        "metrics": {},
        "components": {},
        "penalties": 0,
        "penalty_reasons": [reason],
        "probability_pct": None,
        "verdict": "INFO",
    }


def _backtest_validation_summary(backtest: dict) -> dict:
    metrics = backtest.get("metrics") or {}
    data_quality = backtest.get("data_quality") or {}
    return {
        "score": backtest.get("scanner_score"),
        "backtest_score": backtest.get("backtest_score"),
        "grade": backtest.get("quality_grade"),
        "data_status": backtest.get("data_status"),
        "setup_family": backtest.get("setup_family"),
        "sample_size": backtest.get("sample_size"),
        "same_stock_sample_size": backtest.get("same_stock_sample_size"),
        "peer_sample_size": backtest.get("peer_sample_size"),
        "win_rate": metrics.get("win_rate"),
        "average_r_multiple": metrics.get("average_r_multiple"),
        "false_breakout_rate": metrics.get("false_breakout_rate"),
        "expectancy_per_trade": metrics.get("expectancy_per_trade"),
        "data_quality": data_quality,
    }


def _model_backtest_alignment(model: dict, backtest: dict) -> str:
    grade = backtest.get("quality_grade")
    verdict = model.get("verdict")
    if backtest.get("data_status") == "BACKTEST NOT RUN":
        return "NOT RUN"
    if grade in ("Excellent", "Good") and verdict in ("STRONG BUY", "BUY", "HOLD"):
        return "CONFIRMS"
    if grade in ("Weak", "Poor") and verdict in ("STRONG BUY", "BUY"):
        return "CONFLICTS"
    if grade == "Average":
        return "CONDITIONAL"
    if grade in ("Weak", "Poor") and verdict in ("WAIT", "AVOID"):
        return "CONFIRMS CAUTION"
    return "NEUTRAL"


def _attach_backtest_validation(models: dict[str, dict], backtest: dict) -> dict[str, dict]:
    summary = _backtest_validation_summary(backtest)
    for model in models.values():
        model["backtest_validation"] = {
            **summary,
            "alignment": _model_backtest_alignment(model, backtest),
        }
    return models


def _build_combined_backtest_report(models: dict[str, dict], backtest: dict) -> dict:
    summary = _backtest_validation_summary(backtest)
    model_validations = []
    for key, model in models.items():
        validation = model.get("backtest_validation") or {
            **summary,
            "alignment": _model_backtest_alignment(model, backtest),
        }
        model_validations.append({
            "key": key,
            "model": model.get("model"),
            "model_score": model.get("scanner_score") or model.get("selection_total") or model.get("final_probability"),
            "model_verdict": model.get("verdict"),
            "alignment": validation.get("alignment"),
        })

    alignments = [item["alignment"] for item in model_validations]
    confirmed = len([item for item in alignments if item in ("CONFIRMS", "CONFIRMS CAUTION")])
    conflicts = len([item for item in alignments if item == "CONFLICTS"])
    conditional = len([item for item in alignments if item == "CONDITIONAL"])
    data_status = backtest.get("data_status")

    if data_status == "BACKTEST NOT RUN":
        conclusion = "Backtest validation was not run for this batch snapshot."
    elif conflicts:
        conclusion = "Historical validation conflicts with one or more bullish engine reads."
    elif confirmed >= 3:
        conclusion = "Historical validation broadly supports the six-engine read."
    elif conditional:
        conclusion = "Historical validation is usable but needs live confirmation."
    else:
        conclusion = "Historical validation is mixed or limited by missing data."

    return {
        "title": "Combined Backtest Report",
        "included_engine_count": len(models),
        "included_engines": [item["model"] for item in model_validations],
        "summary": summary,
        "model_validations": model_validations,
        "metrics": backtest.get("metrics") or {},
        "data_quality": backtest.get("data_quality") or {},
        "penalties": backtest.get("penalties", 0),
        "penalty_reasons": backtest.get("penalty_reasons") or [],
        "conclusion": conclusion,
    }


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
    backtest: dict,
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
    if backtest.get("data_status") == "BACKTEST DATA INCOMPLETE":
        caution_bits.append("backtest data is incomplete")
    elif backtest.get("quality_grade") in ("Weak", "Poor"):
        caution_bits.append("historical edge is weak")

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
# COMPOSITE ACROSS SIX VERDICT ENGINES
# ─────────────────────────────────────────────────

def _compute_composite(models: dict[str, dict]) -> dict:
    """Compute the equal-weight composite across the six verdict engines."""
    score_map = {
        "TITAN": models["titan"]["scanner_score"],
        "TITAN_v19": models["titan_v19"]["scanner_score"],
        "Swing_AI": models["swing_ai_v12_2"]["selection_total"],
        "Swing_AI_Hyper": models["swing_ai_v12_1"]["selection_total"],
        "KING": models["king"]["scanner_score"],
        "JP_Pattern": models["jp_pattern_engine"]["scanner_score"],
    }
    probability_map = {
        "TITAN": models["titan"]["probability_pct"],
        "TITAN_v19": models["titan_v19"]["probability_pct"],
        "Swing_AI": models["swing_ai_v12_2"]["final_probability"],
        "Swing_AI_Hyper": models["swing_ai_v12_1"]["final_probability"],
        "KING": models["king"]["probability_pct"],
        "JP_Pattern": models["jp_pattern_engine"]["probability_pct"],
    }
    verdict_map = {
        "TITAN": models["titan"]["verdict"],
        "TITAN_v19": models["titan_v19"]["verdict"],
        "Swing_AI": models["swing_ai_v12_2"]["verdict"],
        "Swing_AI_Hyper": models["swing_ai_v12_1"]["verdict"],
        "KING": models["king"]["verdict"],
        "JP_Pattern": models["jp_pattern_engine"]["verdict"],
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
    elif len([v for v in verdict_map.values() if v == consensus_verdict]) >= (len(verdict_map) // 2 + 1):
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
    Run all active MATE-PRO engines on a stock and return comprehensive results.
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
    jp_pattern = _score_jp_pattern_engine(raw)
    backtest = _backtest_not_run_result() if mode == "batch" else _score_backtest_engine(raw)
    models = _attach_backtest_validation({
        "titan": titan,
        "titan_v19": titan_v19,
        "swing_ai_v12_2": swing_ai,
        "swing_ai_v12_1": swing_ai_hyper,
        "king": king,
        "jp_pattern_engine": jp_pattern,
    }, backtest)
    backtest_report = _build_combined_backtest_report(models, backtest)

    # Generate trade plans
    trade_plans = _generate_trade_plan(raw)

    # Compute composite
    composite = _compute_composite(models)
    fallback_one_line_verdict = _generate_one_line_verdict(symbol, raw, titan, swing_ai, king, backtest, composite, trade_plans)
    if allow_llm_verdict:
        one_line_verdict, one_line_verdict_source = generate_llm_one_line_verdict(
            symbol,
            raw,
            titan,
            swing_ai,
            king,
            backtest,
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
        "models": models,
        "backtest_report": backtest_report,

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
    symbols = [symbol.upper() for symbol in symbols if symbol]
    if MATE_PRO_BATCH_PRELOAD_PROFILES:
        preload_stock_profiles(db, symbols)
    preload_delivery_contexts(db, symbols)
    if mode == "full":
        preload_news_tones(db, symbols)

    worker_count = min(MATE_PRO_BATCH_WORKERS, len(symbols))
    if worker_count <= 1:
        for sym in symbols:
            try:
                result = run_mate_pro_analysis(db, sym, mode=mode, allow_llm_verdict=allow_llm_verdict)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"MATE-PRO failed for {sym}: {e}")
    else:
        from app.database import SessionLocal

        def _run_symbol(sym: str) -> dict | None:
            worker_db = SessionLocal()
            try:
                return run_mate_pro_analysis(
                    worker_db,
                    sym,
                    mode=mode,
                    allow_llm_verdict=allow_llm_verdict,
                )
            finally:
                worker_db.close()

        logger.info("Running MATE-PRO batch with %s workers for %s symbols", worker_count, len(symbols))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(_run_symbol, sym): sym for sym in symbols}
            for future in as_completed(future_map):
                sym = future_map[future]
                try:
                    result = future.result()
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
                reasons.append(f"All {len(ACTIVE_ENGINE_KEYS)} engines agree")
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
