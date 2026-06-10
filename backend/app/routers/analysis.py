"""
Technical Analysis API endpoints.
Detailed analysis, charts, indicators for individual stocks.
Includes MATE-PRO scoring and custom stock lookup.
"""
import logging
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import TechnicalAnalysis, ScreeningResult
from app.services.technical import run_full_analysis, analyze_stock
from app.services.data_fetcher import get_stock_candles, bulk_download_historical
from app.services.screener import _to_python
from app.services.mate_pro import run_mate_pro_analysis, run_mate_pro_batch

logger = logging.getLogger(__name__)
router = APIRouter()


# ── MATE-PRO endpoints (must be BEFORE /{symbol} catch-all) ──

@router.post("/mate-pro/batch")
async def run_mate_pro_batch_analysis(
    symbols: list[str] = Body(None),
    run_id: str = Query(None, description="Use top stocks from this screener run"),
    db: Session = Depends(get_db),
):
    """Run MATE-PRO analysis on multiple stocks."""
    if run_id and not symbols:
        results = db.query(ScreeningResult).filter(
            ScreeningResult.run_id == run_id
        ).order_by(ScreeningResult.composite_score.desc()).limit(20).all()
        symbols = [r.symbol for r in results]

    if not symbols:
        raise HTTPException(status_code=400, detail="Provide symbols or run_id")

    results = run_mate_pro_batch(db, symbols, allow_llm_verdict=False)
    return {
        "total": len(results),
        "stocks": results,
        "summary": {
            "strong_buy": len([r for r in results if r["composite"]["consensus_verdict"] == "STRONG BUY"]),
            "buy": len([r for r in results if r["composite"]["consensus_verdict"] == "BUY"]),
            "hold": len([r for r in results if r["composite"]["consensus_verdict"] == "HOLD"]),
            "wait": len([r for r in results if r["composite"]["consensus_verdict"] == "WAIT"]),
            "avoid": len([r for r in results if r["composite"]["consensus_verdict"] == "AVOID"]),
        },
    }


@router.post("/lookup")
async def lookup_stock(
    symbol: str = Body(..., embed=True),
    db: Session = Depends(get_db),
):
    """
    Look up any NSE stock by symbol — always downloads fresh data,
    then runs full technical analysis + MATE-PRO scoring.
    Works for stocks outside the top 20 / F&O universe.
    """
    from datetime import datetime
    from app.models import Stock, DailyCandle

    symbol = symbol.upper().strip()
    logger.info(f"Looking up stock: {symbol}")

    # Step 1: Always force-download fresh data for lookup
    fetch_result = bulk_download_historical([symbol], db, full_refresh=True)
    if symbol in fetch_result.get("failed", []):
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch data for {symbol}. Check the symbol name — use NSE symbol without .NS suffix (e.g. RELIANCE, TCS, INFY)."
        )

    # Step 2: Ensure Stock record exists (needed for market cap in MATE-PRO)
    stock_record = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock_record:
        # Create a basic record — market cap will be 0 but analysis will work
        stock_record = Stock(symbol=symbol, name=symbol, sector="Unknown", market_cap_cr=0)
        db.add(stock_record)
        db.commit()
        logger.info(f"Created Stock record for {symbol}")

    # Verify we have enough candle data
    candle_count = db.query(DailyCandle).filter(DailyCandle.symbol == symbol).count()
    logger.info(f"Lookup {symbol}: {candle_count} candles in DB")

    if candle_count < 50:
        raise HTTPException(
            status_code=404,
            detail=f"Only {candle_count} days of data for {symbol}. Need at least 50 days for analysis."
        )

    # Step 3: Run technical analysis
    run_id = f"lookup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    ta_result = None
    try:
        ta_result = run_full_analysis(db, symbol, run_id)
    except Exception as e:
        logger.warning(f"TA failed for lookup {symbol}: {e}")

    # Step 4: Run MATE-PRO
    mate_pro = None
    try:
        mate_pro = run_mate_pro_analysis(db, symbol, allow_llm_verdict=True)
    except Exception as e:
        logger.error(f"MATE-PRO failed for lookup {symbol}: {e}", exc_info=True)

    # Step 5: Get chart data
    df = get_stock_candles(db, symbol, days=180)
    chart_result = analyze_stock(df, "daily") if not df.empty else None

    return _to_python({
        "symbol": symbol,
        "run_id": run_id,
        "candle_count": candle_count,
        "technical": ta_result if isinstance(ta_result, dict) and "error" not in ta_result else None,
        "mate_pro": mate_pro,
        "chart_data": chart_result.get("chart_data", []) if chart_result else [],
        "data_status": {
            "fetched": symbol in fetch_result.get("success", []),
            "cached": symbol in fetch_result.get("skipped", []),
        },
        "error": "MATE-PRO analysis could not be completed" if mate_pro is None else None,
    })


# ── Standard analysis endpoints ──

@router.get("/{symbol}/mate-pro")
async def get_mate_pro_analysis(
    symbol: str,
    db: Session = Depends(get_db),
):
    """Run all 3 MATE-PRO models on a stock."""
    symbol = symbol.upper()

    # Auto-download data if missing
    df = get_stock_candles(db, symbol, days=365)
    if df.empty:
        logger.info(f"No data for {symbol}, downloading...")
        bulk_download_historical([symbol], db, full_refresh=False)

    result = run_mate_pro_analysis(db, symbol, allow_llm_verdict=True)
    if not result:
        raise HTTPException(status_code=404, detail=f"Insufficient data for MATE-PRO analysis of {symbol}")
    return result


@router.get("/{symbol}/chart-data")
async def get_chart_data(
    symbol: str,
    timeframe: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(180, ge=30, le=365),
    db: Session = Depends(get_db),
):
    """Get OHLCV + indicator data formatted for charting."""
    symbol = symbol.upper()
    df = get_stock_candles(db, symbol, days=days)

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    df.attrs["symbol"] = symbol

    if timeframe == "weekly":
        from app.services.technical import _resample_to_weekly
        df = _resample_to_weekly(df)
    elif timeframe == "monthly":
        from app.services.technical import _resample_to_monthly
        df = _resample_to_monthly(df)

    result = analyze_stock(df, timeframe)
    if not result:
        raise HTTPException(status_code=404, detail=f"Insufficient data for {symbol}")

    return _to_python({
        "symbol": symbol,
        "timeframe": timeframe,
        "data_points": len(result.get("chart_data", [])),
        "chart_data": result.get("chart_data", []),
        "indicators": {
            "ema_20": result.get("ema_20"),
            "ema_50": result.get("ema_50"),
            "ema_100": result.get("ema_100"),
            "ema_200": result.get("ema_200"),
            "rsi": result.get("rsi"),
            "rsi_signal": result.get("rsi_signal"),
            "bb_upper": result.get("bb_upper"),
            "bb_lower": result.get("bb_lower"),
            "macd": result.get("macd"),
            "macd_crossover": result.get("macd_crossover"),
            "vwap": result.get("vwap"),
            "signal": result.get("signal"),
            "signal_score": result.get("signal_score"),
        },
        "levels": {
            "fibonacci": result.get("fib_levels"),
            "gann": result.get("gann_levels"),
            "support": result.get("support_levels"),
            "resistance": result.get("resistance_levels"),
        },
        "volume_profile": result.get("volume_profile"),
    })


@router.get("/{symbol}")
async def get_analysis(
    symbol: str,
    run_id: str = Query(None, description="Screener run ID. If omitted, computes fresh."),
    db: Session = Depends(get_db),
):
    """Get full technical analysis for a stock across all timeframes."""
    symbol = symbol.upper()

    if run_id:
        cached = db.query(TechnicalAnalysis).filter(
            TechnicalAnalysis.run_id == run_id,
            TechnicalAnalysis.symbol == symbol,
        ).all()

        if cached:
            result = {}
            for ta in cached:
                result[ta.timeframe] = {
                    "ema_20": ta.ema_20,
                    "ema_50": ta.ema_50,
                    "ema_100": ta.ema_100,
                    "ema_200": ta.ema_200,
                    "sma_20": ta.sma_20,
                    "sma_50": ta.sma_50,
                    "bb_upper": ta.bb_upper,
                    "bb_middle": ta.bb_middle,
                    "bb_lower": ta.bb_lower,
                    "bb_width": ta.bb_width,
                    "rsi": ta.rsi,
                    "rsi_signal": ta.rsi_signal,
                    "macd": ta.macd,
                    "macd_signal_line": ta.macd_signal,
                    "macd_histogram": ta.macd_histogram,
                    "macd_crossover": ta.macd_crossover,
                    "vwap": ta.vwap,
                    "volume_profile": ta.volume_profile,
                    "golden_cross": ta.golden_cross,
                    "death_cross": ta.death_cross,
                    "fib_levels": ta.fib_levels,
                    "gann_levels": ta.gann_levels,
                    "support_levels": ta.support_levels,
                    "resistance_levels": ta.resistance_levels,
                    "signal": ta.signal,
                    "signal_score": ta.signal_score,
                }
            return _to_python({"symbol": symbol, "run_id": run_id, "analysis": result})

    from datetime import datetime
    fresh_run_id = run_id or f"adhoc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    results = run_full_analysis(db, symbol, fresh_run_id)
    if "error" in results:
        raise HTTPException(status_code=404, detail=results["error"])

    return _to_python({"symbol": symbol, "run_id": fresh_run_id, "analysis": results})
