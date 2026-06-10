"""
Stock Screener Engine.
Runs all 5 screening reports and cross-references to find top N stocks.

Reports:
  1. 52-Week High proximity
  2. 1-Month New High + Daily Volume surge
  3. 1-Month New High + Monthly Volume + P*V
  4. Open Interest Surge (placeholder until Kite connected)
  5. Index Movers (Nifty 50/Next 50 component momentum)
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import DailyCandle, Stock, ScreeningResult, ScreenerRun
from app.config import (
    MIN_TURNOVER_CR, TOP_N_STOCKS, LOOKBACK_1M_DAYS, NEW_HIGH_TOLERANCE
)

logger = logging.getLogger(__name__)


def _to_python(obj):
    """Recursively convert numpy types to native Python types for JSON serialization."""
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

# Nifty 50 + Next 50 components for index movers report
NIFTY_100 = {
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BHARTIARTL", "BPCL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI",
    "NESTLEIND", "NTPC", "ONGC", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATAMOTORS", "TATAPOWER", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
    # Next 50
    "ABB", "ADANIGREEN", "AMBUJACEM", "BANKBARODA", "BEL", "BOSCHLTD",
    "CANBK", "CDSL", "COLPAL", "DABUR", "DLF", "GODREJCP", "HAL",
    "HAVELLS", "ICICIPRULI", "INDIGO", "INDUSTOWER", "JINDALSTEL",
    "LICI", "LUPIN", "MARICO", "MAXHEALTH", "MOTHERSON", "NHPC",
    "PIDILITIND", "PFC", "RECLTD", "SIEMENS", "SRF", "VEDL", "ZYDUSLIFE",
}


def _compute_stock_metrics(symbol: str, candles: pd.DataFrame) -> dict | None:
    """Compute all screening metrics for a single stock."""
    if candles.empty or len(candles) < 10:
        return None

    closes = candles["close"].values
    highs = candles["high"].values
    lows = candles["low"].values
    volumes = candles["volume"].values
    cmp = closes[-1]

    # 52-week metrics
    high_52w = float(np.max(highs))
    low_52w = float(np.min(lows))
    pct_from_52w = round((cmp / high_52w - 1) * 100, 2) if high_52w > 0 else 0

    # 1-month metrics
    n = min(LOOKBACK_1M_DAYS, len(candles))
    high_1m = float(np.max(highs[-n:]))
    today_high = float(highs[-1])
    is_1m_new_high = today_high >= high_1m * (1 - NEW_HIGH_TOLERANCE)

    # Volume metrics
    today_vol = int(volumes[-1])
    n20 = min(20, len(volumes))
    avg_vol_20d = int(np.mean(volumes[-n20:]))
    avg_vol_1m = int(np.mean(volumes[-n:]))
    vol_ratio_1d = round(today_vol / avg_vol_20d, 2) if avg_vol_20d > 0 else 0

    # Turnover / PV metrics
    pv_today = round(cmp * today_vol / 1e7, 2)  # ₹ Crores
    pv_values_1m = closes[-n:] * volumes[-n:]
    pv_avg_1m = round(float(np.mean(pv_values_1m)) / 1e7, 2)

    # Momentum
    momentum_1d = round((cmp / closes[-2] - 1) * 100, 2) if len(closes) >= 2 else 0
    momentum_1w = round((cmp / closes[-5] - 1) * 100, 2) if len(closes) >= 5 else 0
    momentum_1m = round((cmp / closes[-n] - 1) * 100, 2) if n > 0 else 0
    n3m = min(66, len(closes))
    momentum_3m = round((cmp / closes[-n3m] - 1) * 100, 2) if n3m > 0 else 0

    return _to_python({
        "symbol": symbol,
        "cmp": round(cmp, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_from_52w": pct_from_52w,
        "high_1m": round(high_1m, 2),
        "is_1m_new_high": is_1m_new_high,
        "today_vol": today_vol,
        "avg_vol_20d": avg_vol_20d,
        "avg_vol_1m": avg_vol_1m,
        "vol_ratio_1d": vol_ratio_1d,
        "pv_today": pv_today,
        "pv_avg_1m": pv_avg_1m,
        "turnover_today_cr": pv_today,
        "turnover_avg_cr": pv_avg_1m,
        "momentum_1d": momentum_1d,
        "momentum_1w": momentum_1w,
        "momentum_1m": momentum_1m,
        "momentum_3m": momentum_3m,
        "trading_days": len(candles),
    })


def _report_52w_high(metrics_list: list[dict], top_pct: float = 5.0) -> list[str]:
    """Report 1: Stocks near their 52-week high (within top_pct %)."""
    candidates = [
        m for m in metrics_list
        if m["pct_from_52w"] >= -top_pct and m["turnover_avg_cr"] >= MIN_TURNOVER_CR
    ]
    candidates.sort(key=lambda x: x["pct_from_52w"], reverse=True)
    return [c["symbol"] for c in candidates]


def _report_1m_high_daily_vol(metrics_list: list[dict]) -> list[str]:
    """Report 2: 1-Month New High + Daily Volume surge (vol_ratio > 1.5)."""
    candidates = [
        m for m in metrics_list
        if m["is_1m_new_high"]
        and m["vol_ratio_1d"] >= 1.5
        and m["turnover_avg_cr"] >= MIN_TURNOVER_CR
    ]
    candidates.sort(key=lambda x: x["pv_today"], reverse=True)
    return [c["symbol"] for c in candidates]


def _report_1m_high_monthly_vol(metrics_list: list[dict]) -> list[str]:
    """Report 3: 1-Month New High + strong monthly volume + P*V."""
    candidates = [
        m for m in metrics_list
        if m["is_1m_new_high"]
        and m["pv_avg_1m"] >= MIN_TURNOVER_CR
    ]
    candidates.sort(key=lambda x: x["pv_avg_1m"], reverse=True)
    return [c["symbol"] for c in candidates]


def _report_oi_surge(metrics_list: list[dict]) -> list[str]:
    """
    Report 4: Open Interest Surge.
    Note: OI data requires Kite Connect. For now, we approximate using
    volume surge + positive momentum as a proxy for OI buildup.
    Once Kite is connected, this will use actual F&O OI data.
    """
    candidates = [
        m for m in metrics_list
        if m["vol_ratio_1d"] >= 2.0
        and m["momentum_1d"] > 0
        and m["turnover_avg_cr"] >= MIN_TURNOVER_CR
    ]
    candidates.sort(key=lambda x: x["vol_ratio_1d"], reverse=True)
    return [c["symbol"] for c in candidates]


def _report_index_movers(metrics_list: list[dict]) -> list[str]:
    """Report 5: Nifty 100 component stocks with strong momentum."""
    candidates = [
        m for m in metrics_list
        if m["symbol"] in NIFTY_100
        and m["momentum_1w"] > 0
        and m["turnover_avg_cr"] >= MIN_TURNOVER_CR
    ]
    candidates.sort(key=lambda x: x["momentum_1w"], reverse=True)
    return [c["symbol"] for c in candidates]


def _compute_composite_score(metrics: dict, report_flags: dict) -> float:
    """
    Compute a composite score for ranking.
    Higher is better. Weighs: report appearances, volume, momentum, proximity to 52W high.
    """
    score = 0.0

    # Report appearances (most important - stocks appearing in multiple reports)
    score += report_flags.get("reports_count", 0) * 25

    # Volume strength
    vol_ratio = min(metrics.get("vol_ratio_1d", 0), 5)  # cap at 5x
    score += vol_ratio * 10

    # Momentum (weighted: 1W most important for swing)
    score += metrics.get("momentum_1w", 0) * 3
    score += metrics.get("momentum_1m", 0) * 1.5
    score += metrics.get("momentum_1d", 0) * 2

    # Proximity to 52W high (closer = stronger)
    pct_from_52w = metrics.get("pct_from_52w", -100)
    score += max(0, (10 + pct_from_52w)) * 2  # Bonus if within 10% of 52W high

    # Turnover bonus
    turnover = metrics.get("turnover_avg_cr", 0)
    if turnover >= 100:
        score += 10
    elif turnover >= 50:
        score += 5
    elif turnover >= MIN_TURNOVER_CR:
        score += 2

    return round(score, 2)


def run_screener(db: Session, symbols: list[str], run_id: str | None = None) -> dict:
    """
    Main screener entry point.
    Runs all 5 reports, cross-references, ranks top N stocks.

    Returns: {
        'run_id': str,
        'total_analyzed': int,
        'reports': {
            '52w_high': [...],
            '1m_high_daily_vol': [...],
            '1m_high_monthly_vol': [...],
            'oi_surge': [...],
            'index_movers': [...],
        },
        'top_stocks': [...],  # Top N ranked stocks with all metrics
    }
    """
    import time
    start = time.time()
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")

    # Log the run
    run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
    if not run_record:
        run_record = ScreenerRun(
            run_id=run_id,
            started_at=datetime.utcnow(),
            status="running",
            total_stocks=len(symbols),
        )
        db.add(run_record)
    else:
        run_record.started_at = run_record.started_at or datetime.utcnow()
        run_record.status = "running"
        run_record.total_stocks = len(symbols)
    db.commit()

    # Step 1: Compute metrics for all stocks
    logger.info(f"Computing metrics for {len(symbols)} stocks...")
    all_metrics = []
    for sym in symbols:
        candles = db.query(DailyCandle).filter(
            DailyCandle.symbol == sym,
            DailyCandle.date >= date.today() - timedelta(days=400)
        ).order_by(DailyCandle.date).all()

        if not candles or len(candles) < 10:
            continue

        df = pd.DataFrame([{
            "date": c.date, "open": c.open, "high": c.high,
            "low": c.low, "close": c.close, "volume": c.volume,
        } for c in candles])

        metrics = _compute_stock_metrics(sym, df)
        if metrics:
            # Get market cap from Stock table
            stock = db.query(Stock).filter(Stock.symbol == sym).first()
            if stock:
                metrics["market_cap_cr"] = stock.market_cap_cr
            all_metrics.append(metrics)

    logger.info(f"Computed metrics for {len(all_metrics)} stocks")

    # Step 2: Run all 5 reports
    reports = {
        "52w_high": _report_52w_high(all_metrics),
        "1m_high_daily_vol": _report_1m_high_daily_vol(all_metrics),
        "1m_high_monthly_vol": _report_1m_high_monthly_vol(all_metrics),
        "oi_surge": _report_oi_surge(all_metrics),
        "index_movers": _report_index_movers(all_metrics),
    }

    for name, syms in reports.items():
        logger.info(f"Report '{name}': {len(syms)} stocks")

    # Step 3: Cross-reference - count appearances across reports
    appearance_count = {}
    for report_name, report_symbols in reports.items():
        for sym in report_symbols:
            if sym not in appearance_count:
                appearance_count[sym] = {"reports": [], "count": 0}
            appearance_count[sym]["reports"].append(report_name)
            appearance_count[sym]["count"] += 1

    # Step 4: Compute composite scores and save results
    for metrics in all_metrics:
        sym = metrics["symbol"]
        app = appearance_count.get(sym, {"reports": [], "count": 0})

        report_flags = {
            "in_52w_high_report": "52w_high" in app["reports"],
            "in_1m_high_daily_vol": "1m_high_daily_vol" in app["reports"],
            "in_1m_high_monthly_vol": "1m_high_monthly_vol" in app["reports"],
            "in_oi_surge": "oi_surge" in app["reports"],
            "in_index_movers": "index_movers" in app["reports"],
            "reports_count": app["count"],
        }

        composite = _compute_composite_score(metrics, report_flags)
        metrics.update(report_flags)
        metrics["composite_score"] = composite

        result = ScreeningResult(
            run_id=run_id,
            symbol=sym,
            cmp=metrics["cmp"],
            high_52w=metrics["high_52w"],
            low_52w=metrics["low_52w"],
            pct_from_52w=metrics["pct_from_52w"],
            high_1m=metrics["high_1m"],
            is_1m_new_high=metrics["is_1m_new_high"],
            today_vol=metrics["today_vol"],
            avg_vol_20d=metrics["avg_vol_20d"],
            avg_vol_1m=metrics["avg_vol_1m"],
            vol_ratio_1d=metrics["vol_ratio_1d"],
            pv_today=metrics["pv_today"],
            pv_avg_1m=metrics["pv_avg_1m"],
            turnover_today_cr=metrics["turnover_today_cr"],
            turnover_avg_cr=metrics["turnover_avg_cr"],
            momentum_1d=metrics["momentum_1d"],
            momentum_1w=metrics["momentum_1w"],
            momentum_1m=metrics["momentum_1m"],
            momentum_3m=metrics["momentum_3m"],
            market_cap_cr=metrics.get("market_cap_cr", 0),
            composite_score=composite,
            **report_flags,
        )
        db.add(result)

    db.commit()

    # Step 5: Get top N
    top_results = db.query(ScreeningResult).filter(
        ScreeningResult.run_id == run_id
    ).order_by(ScreeningResult.composite_score.desc()).limit(TOP_N_STOCKS).all()

    top_stocks = []
    for r in top_results:
        top_stocks.append(_to_python({
            "rank": len(top_stocks) + 1,
            "symbol": r.symbol,
            "cmp": r.cmp,
            "high_52w": r.high_52w,
            "pct_from_52w": r.pct_from_52w,
            "is_1m_new_high": r.is_1m_new_high,
            "vol_ratio_1d": r.vol_ratio_1d,
            "turnover_avg_cr": r.turnover_avg_cr,
            "momentum_1w": r.momentum_1w,
            "momentum_1m": r.momentum_1m,
            "reports_count": r.reports_count,
            "composite_score": r.composite_score,
            "market_cap_cr": r.market_cap_cr,
            "reports": {
                "52w_high": r.in_52w_high_report,
                "1m_high_daily_vol": r.in_1m_high_daily_vol,
                "1m_high_monthly_vol": r.in_1m_high_monthly_vol,
                "oi_surge": r.in_oi_surge,
                "index_movers": r.in_index_movers,
            },
        }))

    elapsed = round(time.time() - start, 2)

    # Update run record
    run_record.completed_at = datetime.utcnow()
    run_record.status = "completed"
    run_record.filtered_stocks = len(all_metrics)
    run_record.top_stocks = len(top_stocks)
    db.commit()

    logger.info(f"Screener complete: top {len(top_stocks)} stocks in {elapsed}s")

    return {
        "run_id": run_id,
        "total_analyzed": len(all_metrics),
        "elapsed_seconds": elapsed,
        "reports": {k: v[:30] for k, v in reports.items()},  # Limit report lists
        "top_stocks": top_stocks,
        "all_metrics": all_metrics,  # All analyzed stocks for MATE-PRO
    }
