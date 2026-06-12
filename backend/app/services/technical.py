"""
Technical Analysis Engine.
Computes all indicators for a given stock across daily, weekly, monthly timeframes.
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import TechnicalAnalysis, DailyCandle
from app.config import (
    EMA_PERIODS, RSI_PERIOD, BOLLINGER_PERIOD, BOLLINGER_STD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL
)

logger = logging.getLogger(__name__)


def _resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly."""
    weekly = df.resample("W").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return weekly


def _resample_to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to monthly."""
    monthly = df.resample("ME").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return monthly


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def _calc_sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return series.rolling(window=period).mean()


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _calc_bollinger(series: pd.Series, period: int = 20, std: float = 2.0) -> dict:
    """Calculate Bollinger Bands."""
    middle = series.rolling(window=period).mean()
    rolling_std = series.rolling(window=period).std()
    upper = middle + (rolling_std * std)
    lower = middle - (rolling_std * std)
    width = ((upper - lower) / middle * 100) if middle.iloc[-1] != 0 else pd.Series([0])
    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "width": width,
    }


def _calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Calculate MACD."""
    ema_fast = _calc_ema(series, fast)
    ema_slow = _calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _calc_ema(macd_line, signal)
    histogram = macd_line - signal_line

    
    crossover = "none"
    if len(histogram) >= 2:
        if histogram.iloc[-1] > 0 and histogram.iloc[-2] <= 0:
            crossover = "bullish"
        elif histogram.iloc[-1] < 0 and histogram.iloc[-2] >= 0:
            crossover = "bearish"

    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
        "crossover": crossover,
    }


def _calc_vwap(df: pd.DataFrame) -> float:
    """Calculate Volume Weighted Average Price."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical_price * df["volume"]).sum() / df["volume"].sum()
    return round(float(vwap), 2)


def _calc_fibonacci_levels(high: float, low: float) -> dict:
    """Calculate Fibonacci retracement levels."""
    diff = high - low
    return {
        "0.0": round(low, 2),
        "0.236": round(low + diff * 0.236, 2),
        "0.382": round(low + diff * 0.382, 2),
        "0.5": round(low + diff * 0.5, 2),
        "0.618": round(low + diff * 0.618, 2),
        "0.786": round(low + diff * 0.786, 2),
        "1.0": round(high, 2),
        "1.272": round(high + diff * 0.272, 2),
        "1.618": round(high + diff * 0.618, 2),
    }


def _calc_gann_levels(cmp: float) -> dict:
    """
    Calculate Gann square of 9 support/resistance levels.
    Uses the square root method around the current price.
    """
    sqrt_price = np.sqrt(cmp)
    levels = {}
    for i in range(-4, 5):
        level = (sqrt_price + i * 0.25) ** 2
        levels[f"level_{i}"] = round(float(level), 2)
    return levels


def _calc_volume_profile(df: pd.DataFrame, bins: int = 20) -> list[dict]:
    """
    Calculate Volume Profile - volume at each price level.
    Returns list of {price_low, price_high, volume, pct} sorted by price.
    """
    if df.empty:
        return []

    price_min = df["low"].min()
    price_max = df["high"].max()
    step = (price_max - price_min) / bins if bins > 0 else 1

    profile = []
    total_vol = df["volume"].sum()
    for i in range(bins):
        lo = price_min + i * step
        hi = lo + step
        mask = (df["close"] >= lo) & (df["close"] < hi)
        vol = int(df.loc[mask, "volume"].sum())
        profile.append({
            "price_low": round(lo, 2),
            "price_high": round(hi, 2),
            "volume": vol,
            "pct": round(vol / total_vol * 100, 2) if total_vol > 0 else 0,
        })
    return profile


def _detect_golden_death_cross(ema_50: pd.Series, ema_200: pd.Series) -> dict:
    """Detect Golden Cross (50 > 200) or Death Cross (50 < 200)."""
    if len(ema_50) < 2 or len(ema_200) < 2:
        return {"golden_cross": False, "death_cross": False}

    prev_diff = ema_50.iloc[-2] - ema_200.iloc[-2]
    curr_diff = ema_50.iloc[-1] - ema_200.iloc[-1]

    return {
        "golden_cross": prev_diff <= 0 and curr_diff > 0,
        "death_cross": prev_diff >= 0 and curr_diff < 0,
    }


def _compute_signal(rsi: float, macd_cross: str, ema_20, ema_50,
                     ema_200, cmp: float, bb_pos: str) -> dict:
    """
    Compute overall trading signal from multiple indicators.
    Returns {signal: str, score: float (-100 to 100)}.
    """
    score = 0
    
    ema_20 = ema_20 if ema_20 is not None else cmp
    ema_50 = ema_50 if ema_50 is not None else cmp
    ema_200 = ema_200 if ema_200 is not None else 0

    # RSI contribution (-30 to +30)
    if rsi is None:
        rsi = 50
    if rsi < 30:
        score += 25  # Oversold = buy opportunity
    elif rsi < 40:
        score += 15
    elif rsi > 70:
        score -= 25  
    elif rsi > 60:
        score -= 10
    else:
        score += 5  

    
    if macd_cross == "bullish":
        score += 20
    elif macd_cross == "bearish":
        score -= 20

    # EMA trend (-30 to +30)
    if cmp > ema_20 > ema_50:
        score += 25  # Strong uptrend
    elif cmp > ema_20:
        score += 15
    elif cmp < ema_20 < ema_50:
        score -= 25  # Strong downtrend
    elif cmp < ema_20:
        score -= 15

    # Price vs 200 EMA (-20 to +20)
    if ema_200 and ema_200 > 0:
        if cmp > ema_200:
            score += 15
        else:
            score -= 15

    # Clamp to -100 to 100
    score = max(-100, min(100, score))

    if score >= 50:
        signal = "strong_buy"
    elif score >= 20:
        signal = "buy"
    elif score <= -50:
        signal = "strong_sell"
    elif score <= -20:
        signal = "sell"
    else:
        signal = "neutral"

    return {"signal": signal, "score": round(score, 2)}


def analyze_stock(df: pd.DataFrame, timeframe: str = "daily") -> dict:
    """
    Run full technical analysis on a DataFrame of OHLCV data.
    Returns dict of all computed indicators.
    """
    if df.empty or len(df) < 20:
        return {}

    close = df["close"]
    cmp = float(close.iloc[-1])

    # EMAs
    emas = {}
    for period in EMA_PERIODS:
        ema = _calc_ema(close, period)
        emas[f"ema_{period}"] = round(float(ema.iloc[-1]), 2) if len(ema) >= period else None

    # SMAs
    sma_20 = _calc_sma(close, 20)
    sma_50 = _calc_sma(close, 50)

    # RSI
    rsi_series = _calc_rsi(close, RSI_PERIOD)
    rsi_val = round(float(rsi_series.iloc[-1]), 2) if not rsi_series.empty else 50
    rsi_signal = "oversold" if rsi_val < 30 else ("overbought" if rsi_val > 70 else "neutral")

    # Bollinger Bands
    bb = _calc_bollinger(close, BOLLINGER_PERIOD, BOLLINGER_STD)
    bb_upper = round(float(bb["upper"].iloc[-1]), 2) if not bb["upper"].empty else None
    bb_middle = round(float(bb["middle"].iloc[-1]), 2) if not bb["middle"].empty else None
    bb_lower = round(float(bb["lower"].iloc[-1]), 2) if not bb["lower"].empty else None
    bb_width = round(float(bb["width"].iloc[-1]), 2) if not bb["width"].empty else None

    bb_pos = "neutral"
    if bb_upper and bb_lower:
        if cmp >= bb_upper:
            bb_pos = "above_upper"
        elif cmp <= bb_lower:
            bb_pos = "below_lower"

    # MACD
    macd = _calc_macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    macd_val = round(float(macd["macd"].iloc[-1]), 2) if not macd["macd"].empty else 0
    macd_sig = round(float(macd["signal"].iloc[-1]), 2) if not macd["signal"].empty else 0
    macd_hist = round(float(macd["histogram"].iloc[-1]), 2) if not macd["histogram"].empty else 0

    # VWAP (for daily/intraday)
    vwap = _calc_vwap(df)

    # Volume Profile
    vol_profile = _calc_volume_profile(df)

    # Golden / Death Cross
    ema_50_series = _calc_ema(close, 50)
    ema_200_series = _calc_ema(close, 200)
    cross = _detect_golden_death_cross(ema_50_series, ema_200_series)

    # Fibonacci levels (from 52W high/low)
    fib = _calc_fibonacci_levels(float(df["high"].max()), float(df["low"].min()))

    # Gann levels
    gann = _calc_gann_levels(cmp)

    # Support / Resistance from recent pivots
    recent = df.tail(60)
    support_levels = sorted(recent["low"].nsmallest(5).unique().tolist())[:3]
    resistance_levels = sorted(recent["high"].nlargest(5).unique().tolist(), reverse=True)[:3]

    # Overall signal
    sig = _compute_signal(
        rsi_val, macd["crossover"],
        emas.get("ema_20", cmp), emas.get("ema_50", cmp),
        emas.get("ema_200", cmp), cmp, bb_pos
    )

    # Build chart data for frontend (last 60 data points)
    chart_len = min(60, len(df))
    chart_df = df.tail(chart_len)

    chart_data = []
    ema_20_full = _calc_ema(close, 20)
    ema_50_full = _calc_ema(close, 50)
    rsi_full = _calc_rsi(close, RSI_PERIOD)
    bb_full = _calc_bollinger(close, BOLLINGER_PERIOD, BOLLINGER_STD)
    macd_full = _calc_macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)

    for i in range(-chart_len, 0):
        idx = len(df) + i
        row = df.iloc[idx]
        dt = df.index[idx]
        point = {
            "date": dt.strftime("%Y-%m-%d"),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
        }
        # Add overlays
        if idx < len(ema_20_full):
            point["ema_20"] = round(float(ema_20_full.iloc[idx]), 2)
        if idx < len(ema_50_full):
            point["ema_50"] = round(float(ema_50_full.iloc[idx]), 2)
        if idx < len(bb_full["upper"]):
            point["bb_upper"] = round(float(bb_full["upper"].iloc[idx]), 2)
            point["bb_lower"] = round(float(bb_full["lower"].iloc[idx]), 2)
        if idx < len(rsi_full):
            point["rsi"] = round(float(rsi_full.iloc[idx]), 2)
        if idx < len(macd_full["macd"]):
            point["macd"] = round(float(macd_full["macd"].iloc[idx]), 2)
            point["macd_signal"] = round(float(macd_full["signal"].iloc[idx]), 2)
            point["macd_histogram"] = round(float(macd_full["histogram"].iloc[idx]), 2)

        chart_data.append(point)

    return {
        "symbol": df.attrs.get("symbol", ""),
        "timeframe": timeframe,
        "cmp": cmp,
        **emas,
        "sma_20": round(float(sma_20.iloc[-1]), 2) if len(sma_20) >= 20 else None,
        "sma_50": round(float(sma_50.iloc[-1]), 2) if len(sma_50) >= 50 else None,
        "rsi": rsi_val,
        "rsi_signal": rsi_signal,
        "bb_upper": bb_upper,
        "bb_middle": bb_middle,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
        "macd": macd_val,
        "macd_signal_line": macd_sig,
        "macd_histogram": macd_hist,
        "macd_crossover": macd["crossover"],
        "vwap": vwap,
        "volume_profile": vol_profile,
        "golden_cross": cross["golden_cross"],
        "death_cross": cross["death_cross"],
        "fib_levels": fib,
        "gann_levels": gann,
        "support_levels": [round(s, 2) for s in support_levels],
        "resistance_levels": [round(r, 2) for r in resistance_levels],
        "signal": sig["signal"],
        "signal_score": sig["score"],
        "chart_data": chart_data,
    }


def run_full_analysis(db: Session, symbol: str, run_id: str) -> dict:
    """
    Run technical analysis for a stock across all 3 timeframes.
    Saves results to DB and returns the analysis dict.
    """
    from app.services.data_fetcher import get_stock_candles

    daily_df = get_stock_candles(db, symbol, days=365)
    if daily_df.empty:
        return {"error": f"No data for {symbol}"}

    daily_df.attrs["symbol"] = symbol

    results = {}

    # Daily analysis
    daily_result = analyze_stock(daily_df, "daily")
    results["daily"] = daily_result

    # Weekly analysis
    weekly_df = _resample_to_weekly(daily_df)
    weekly_df.attrs["symbol"] = symbol
    if len(weekly_df) >= 20:
        results["weekly"] = analyze_stock(weekly_df, "weekly")

    # Monthly analysis
    monthly_df = _resample_to_monthly(daily_df)
    monthly_df.attrs["symbol"] = symbol
    if len(monthly_df) >= 12:
        results["monthly"] = analyze_stock(monthly_df, "monthly")

    # Save to DB
    for tf, analysis in results.items():
        if not analysis or "error" in analysis:
            continue
        ta = TechnicalAnalysis(
            run_id=run_id,
            symbol=symbol,
            timeframe=tf,
            ema_20=analysis.get("ema_20"),
            ema_50=analysis.get("ema_50"),
            ema_100=analysis.get("ema_100"),
            ema_200=analysis.get("ema_200"),
            sma_20=analysis.get("sma_20"),
            sma_50=analysis.get("sma_50"),
            bb_upper=analysis.get("bb_upper"),
            bb_middle=analysis.get("bb_middle"),
            bb_lower=analysis.get("bb_lower"),
            bb_width=analysis.get("bb_width"),
            rsi=analysis.get("rsi"),
            rsi_signal=analysis.get("rsi_signal"),
            macd=analysis.get("macd"),
            macd_signal=analysis.get("macd_signal_line"),
            macd_histogram=analysis.get("macd_histogram"),
            macd_crossover=analysis.get("macd_crossover"),
            vwap=analysis.get("vwap"),
            volume_profile=analysis.get("volume_profile"),
            golden_cross=analysis.get("golden_cross", False),
            death_cross=analysis.get("death_cross", False),
            fib_levels=analysis.get("fib_levels"),
            gann_levels=analysis.get("gann_levels"),
            support_levels=analysis.get("support_levels"),
            resistance_levels=analysis.get("resistance_levels"),
            signal=analysis.get("signal"),
            signal_score=analysis.get("signal_score", 0),
        )
        db.add(ta)

    db.commit()
    return results
