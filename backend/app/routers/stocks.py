"""
Stock data API endpoints.
Price history, delivery data, OHLCV.
"""
import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import DailyCandle, Stock, DeliveryData
from app.services.live_data import get_live_quote, get_live_quotes_batch

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/universe")
def get_universe(
    fno_only: bool = Query(False, description="Return only F&O stocks"),
    db: Session = Depends(get_db),
):
    """Get the current active stock universe."""
    query = db.query(Stock).filter(Stock.is_active == True)
    if fno_only:
        query = query.filter(Stock.is_fno == True)
    stocks = query.order_by(Stock.market_cap_cr.desc()).all()

    return {
        "total": len(stocks),
        "stocks": [
            {
                "symbol": s.symbol,
                "name": s.name,
                "sector": s.sector,
                "market_cap_cr": s.market_cap_cr,
                "lot_size": s.lot_size,
            }
            for s in stocks
        ],
    }


@router.get("/{symbol}/price-history")
def get_price_history(
    symbol: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """
    Get price history for a stock.
    Returns OHLCV with prev_close, change, change_pct.
    """
    cutoff = date.today() - timedelta(days=days + 5)
    candles = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol.upper(),
        DailyCandle.date >= cutoff,
    ).order_by(DailyCandle.date).all()

    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    history = []
    for i, c in enumerate(candles):
        prev_close = candles[i - 1].close if i > 0 else c.open
        change = round(c.close - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
        value_cr = round(c.close * c.volume / 1e7, 2)

        history.append({
            "date": c.date.isoformat(),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "prev_close": round(prev_close, 2),
            "close": c.close,
            "change": change,
            "change_pct": change_pct,
            "volume": c.volume,
            "value_cr": value_cr,
        })

    # Trim to requested days
    history = history[-days:]

    return {
        "symbol": symbol.upper(),
        "days": len(history),
        "history": history,
    }


@router.get("/{symbol}/delivery")
def get_delivery_data(
    symbol: str,
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """
    Get delivery volume data for a stock.
    Note: Delivery data requires NSE bhavcopy. For now, returns volume-based estimates.
    """
    cutoff = date.today() - timedelta(days=days + 5)
    candles = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol.upper(),
        DailyCandle.date >= cutoff,
    ).order_by(DailyCandle.date).all()

    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    # Compute rolling 5-day average volume
    volumes = [c.volume for c in candles]
    delivery_data = []

    for i, c in enumerate(candles):
        # Rolling week average
        start_idx = max(0, i - 4)
        rolling_avg = int(sum(volumes[start_idx:i + 1]) / (i - start_idx + 1))

        # Estimate delivery as ~40-60% of volume (placeholder until NSE data)
        est_delivery_pct = 45.0  # placeholder
        est_delivery = int(c.volume * est_delivery_pct / 100)

        prev_close = candles[i - 1].close if i > 0 else c.open
        price_change_pct = round((c.close - prev_close) / prev_close * 100, 2) if prev_close else 0

        # Volume vs weekly average insight
        if rolling_avg > 0:
            vs_avg_ratio = c.volume / rolling_avg
            if vs_avg_ratio > 1.5:
                insight = "High volume"
            elif vs_avg_ratio > 1.2:
                insight = "Above average"
            elif vs_avg_ratio < 0.7:
                insight = "Low volume"
            else:
                insight = "Normal"
        else:
            insight = "N/A"

        delivery_data.append({
            "date": c.date.isoformat(),
            "traded_volume": c.volume,
            "delivery_volume": est_delivery,
            "delivery_pct": est_delivery_pct,
            "price_change_pct": price_change_pct,
            "insight": insight,
            "rolling_week_avg_vol": rolling_avg,
            "close_price": c.close,
        })

    delivery_data = delivery_data[-days:]

    return {
        "symbol": symbol.upper(),
        "days": len(delivery_data),
        "note": "Delivery data is estimated. Actual data requires NSE bhavcopy integration.",
        "data": delivery_data,
    }




@router.get("/{symbol}/quote")
def get_live_stock_quote(symbol: str):
    """Return a small live quote snapshot for a given symbol.

    Tries the NSE API first, then falls back to yfinance.
    """
    q = get_live_quote(symbol)
    if not q or q.get("last_price") is None:
        raise HTTPException(status_code=404, detail=f"No live data for {symbol}")
    return q



@router.get("/quotes")
def get_live_quotes(
    symbols: str = Query(..., description="Comma separated symbols, e.g. RELIANCE,INFY"),
):
    """Get live quotes for multiple symbols in one request.

    Example: `/api/stocks/quotes?symbols=RELIANCE,INFY`.
    """
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No symbols provided")

    return get_live_quotes_batch(syms)
