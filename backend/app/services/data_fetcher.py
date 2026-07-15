"""
Bulk historical data fetcher.

Downloads 1 year of daily OHLCV for all stocks via yfinance and stores the
data in SQLite. Large universes are chunked so full-NSE runs stay reliable.
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import HISTORICAL_DOWNLOAD_BATCH_SIZE, LOOKBACK_DAYS, NSE_SUFFIX
from app.models import DailyCandle

logger = logging.getLogger(__name__)


def _yf_symbol(symbol: str) -> str:
    """Convert NSE symbol to yfinance format."""
    s = symbol.replace("&", "%26") if "&" in symbol else symbol
    return f"{s}{NSE_SUFFIX}"


def _get_last_date_in_db(db: Session, symbol: str) -> date | None:
    result = db.query(func.max(DailyCandle.date)).filter(DailyCandle.symbol == symbol).scalar()
    return result


def _normalize_yf_ohlcv(data: pd.DataFrame, yf_symbol: str | None = None) -> pd.DataFrame:
    """Return a single-symbol OHLCV frame from yfinance's old or new column layout."""
    if data is None or data.empty:
        return pd.DataFrame()

    df = data.copy()
    if isinstance(df.columns, pd.MultiIndex):
        if yf_symbol and yf_symbol in df.columns.get_level_values(0):
            df = df.xs(yf_symbol, axis=1, level=0)
        elif yf_symbol and yf_symbol in df.columns.get_level_values(-1):
            df = df.xs(yf_symbol, axis=1, level=-1)
        elif len(df.columns.levels[0]) == 1:
            df = df.droplevel(0, axis=1)
        elif len(df.columns.levels[-1]) == 1:
            df = df.droplevel(-1, axis=1)
        else:
            return pd.DataFrame()

    df.columns = [str(col).strip().title().replace(" ", " ") for col in df.columns]
    if "Adj Close" in df.columns and "Close" not in df.columns:
        df = df.rename(columns={"Adj Close": "Close"})
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required):
        return pd.DataFrame()
    return df[required].dropna(subset=["Close"]).copy()


def _get_last_dates_map(db: Session, symbols: list[str]) -> dict[str, date]:
    rows = db.query(
        DailyCandle.symbol,
        func.max(DailyCandle.date),
    ).filter(
        DailyCandle.symbol.in_(symbols),
    ).group_by(DailyCandle.symbol).all()
    return {symbol: latest_date for symbol, latest_date in rows if latest_date}


def _get_existing_dates_map(
    db: Session,
    symbols: list[str],
    start_date: date,
) -> dict[str, set[date]]:
    existing_dates: dict[str, set[date]] = defaultdict(set)
    rows = db.query(DailyCandle.symbol, DailyCandle.date).filter(
        DailyCandle.symbol.in_(symbols),
        DailyCandle.date >= start_date,
    ).all()
    for symbol, candle_date in rows:
        existing_dates[symbol].add(candle_date)
    return existing_dates


def _iter_symbol_batches(symbols: list[str]) -> list[list[str]]:
    return [
        symbols[i:i + HISTORICAL_DOWNLOAD_BATCH_SIZE]
        for i in range(0, len(symbols), HISTORICAL_DOWNLOAD_BATCH_SIZE)
    ]


def bulk_download_historical(
    symbols: list[str],
    db: Session,
    full_refresh: bool = False,
) -> dict:
    """
    Download historical data for all symbols in bulk.

    Returns: {
        "success": [...],
        "failed": [...],
        "skipped": [...],
        "total_candles": int,
        "elapsed_seconds": float,
    }
    """
    import time

    start_time = time.time()
    result = {"success": [], "failed": [], "skipped": [], "total_candles": 0}
    today = date.today()
    start_date = today - timedelta(days=LOOKBACK_DAYS)

    last_dates = {} if full_refresh else _get_last_dates_map(db, symbols)
    symbols_to_fetch: dict[str, date] = {}
    for sym in symbols:
        if full_refresh:
            symbols_to_fetch[sym] = start_date
            continue

        last_date = last_dates.get(sym)
        if last_date and last_date >= today - timedelta(days=1):
            result["skipped"].append(sym)
        elif last_date:
            symbols_to_fetch[sym] = last_date + timedelta(days=1)
        else:
            symbols_to_fetch[sym] = start_date

    if not symbols_to_fetch:
        logger.info("All symbols up to date, nothing to fetch")
        result["elapsed_seconds"] = round(time.time() - start_time, 2)
        return result

    logger.info("Fetching data for %s symbols...", len(symbols_to_fetch))

    date_groups: dict[date, list[str]] = {}
    for sym, fetch_start in symbols_to_fetch.items():
        date_groups.setdefault(fetch_start, []).append(sym)

    for fetch_start, group_symbols in date_groups.items():
        for batch_symbols in _iter_symbol_batches(group_symbols):
            yf_symbols = [_yf_symbol(s) for s in batch_symbols]
            existing_dates_map = (
                {}
                if full_refresh
                else _get_existing_dates_map(db, batch_symbols, fetch_start - timedelta(days=7))
            )

            try:
                logger.info(
                    "yfinance batch: %s stocks from %s to %s",
                    len(batch_symbols),
                    fetch_start,
                    today,
                )
                data = yf.download(
                    " ".join(yf_symbols),
                    start=fetch_start.isoformat(),
                    end=(today + timedelta(days=1)).isoformat(),
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )

                if data.empty:
                    logger.warning("yfinance returned empty dataframe")
                    result["failed"].extend(batch_symbols)
                    continue

                if full_refresh:
                    db.query(DailyCandle).filter(
                        DailyCandle.symbol.in_(batch_symbols),
                        DailyCandle.date >= fetch_start,
                    ).delete(synchronize_session=False)
                    db.commit()

                pending_objects = []

                for sym in batch_symbols:
                    yf_sym = _yf_symbol(sym)
                    try:
                        if len(batch_symbols) == 1:
                            df = _normalize_yf_ohlcv(data, yf_sym)
                        elif isinstance(data.columns, pd.MultiIndex):
                            df = _normalize_yf_ohlcv(data, yf_sym)
                        else:
                            df = pd.DataFrame()

                        if df.empty or df.dropna(how="all").empty:
                            result["failed"].append(sym)
                            continue

                        candles_added = 0
                        existing_dates = existing_dates_map.get(sym, set())

                        for idx, row in df.iterrows():
                            candle_date = idx.date() if hasattr(idx, "date") else idx
                            if candle_date in existing_dates:
                                continue

                            pending_objects.append(DailyCandle(
                                symbol=sym,
                                date=candle_date,
                                open=round(float(row.get("Open", 0)), 2),
                                high=round(float(row.get("High", 0)), 2),
                                low=round(float(row.get("Low", 0)), 2),
                                close=round(float(row.get("Close", 0)), 2),
                                volume=int(row.get("Volume", 0)),
                                adj_close=round(float(row.get("Close", 0)), 2),
                            ))
                            existing_dates.add(candle_date)
                            candles_added += 1

                        if candles_added > 0:
                            result["success"].append(sym)
                            result["total_candles"] += candles_added
                        else:
                            result["skipped"].append(sym)

                    except Exception as exc:
                        logger.warning("Failed to process %s: %s", sym, exc, exc_info=True)
                        result["failed"].append(sym)

                try:
                    if pending_objects:
                        db.bulk_save_objects(pending_objects)
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    logger.warning("Duplicate candle conflict in batch %s: %s", batch_symbols, exc)
                    result["failed"].extend([sym for sym in batch_symbols if sym not in result["failed"]])

            except Exception as exc:
                db.rollback()
                logger.error("yfinance batch download failed: %s", exc)
                result["failed"].extend(batch_symbols)

    result["elapsed_seconds"] = round(time.time() - start_time, 2)
    logger.info(
        "Data fetch complete: %s success, %s failed, %s skipped, %s candles in %ss",
        len(result["success"]),
        len(result["failed"]),
        len(result["skipped"]),
        result["total_candles"],
        result["elapsed_seconds"],
    )
    return result


def ensure_symbol_history(
    symbol: str,
    db: Session,
    years: int = 5,
    force_refresh: bool = False,
) -> dict:
    """
    Ensure one symbol has deep adjusted daily history for full-detail analysis.
    This is intentionally single-symbol so dashboard batch runs stay fast.
    """
    import time

    symbol = symbol.upper()
    start_time = time.time()
    today = date.today()
    start_date = today - timedelta(days=years * 365 + 10)
    latest_date = _get_last_date_in_db(db, symbol)
    earliest_date = db.query(func.min(DailyCandle.date)).filter(DailyCandle.symbol == symbol).scalar()

    if (
        not force_refresh
        and earliest_date
        and earliest_date <= start_date + timedelta(days=14)
        and latest_date
        and latest_date >= today - timedelta(days=5)
    ):
        return {
            "symbol": symbol,
            "status": "cached",
            "added": 0,
            "earliest_date": earliest_date.isoformat(),
            "latest_date": latest_date.isoformat(),
            "elapsed_seconds": round(time.time() - start_time, 2),
        }

    yf_symbol = _yf_symbol(symbol)
    try:
        logger.info("Fetching deep adjusted history for %s from %s", symbol, start_date)
        df = yf.download(
            yf_symbol,
            start=start_date.isoformat(),
            end=(today + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if df.empty:
            return {
                "symbol": symbol,
                "status": "failed",
                "error": "empty yfinance response",
                "elapsed_seconds": round(time.time() - start_time, 2),
            }

        df = _normalize_yf_ohlcv(df, yf_symbol)
        if df.empty:
            return {
                "symbol": symbol,
                "status": "failed",
                "error": "empty normalized yfinance OHLCV response",
                "elapsed_seconds": round(time.time() - start_time, 2),
            }
        db.query(DailyCandle).filter(
            DailyCandle.symbol == symbol,
            DailyCandle.date >= start_date,
        ).delete(synchronize_session=False)
        db.commit()

        pending_objects = []
        for idx, row in df.iterrows():
            candle_date = idx.date() if hasattr(idx, "date") else idx
            pending_objects.append(DailyCandle(
                symbol=symbol,
                date=candle_date,
                open=round(float(row.get("Open", 0)), 2),
                high=round(float(row.get("High", 0)), 2),
                low=round(float(row.get("Low", 0)), 2),
                close=round(float(row.get("Close", 0)), 2),
                volume=int(row.get("Volume", 0)),
                adj_close=round(float(row.get("Close", 0)), 2),
            ))

        if pending_objects:
            db.bulk_save_objects(pending_objects)
        db.commit()

        return {
            "symbol": symbol,
            "status": "fetched",
            "added": len(pending_objects),
            "earliest_date": min(obj.date for obj in pending_objects).isoformat() if pending_objects else None,
            "latest_date": max(obj.date for obj in pending_objects).isoformat() if pending_objects else None,
            "elapsed_seconds": round(time.time() - start_time, 2),
        }
    except Exception as exc:
        db.rollback()
        logger.warning("Deep history fetch failed for %s: %s", symbol, exc, exc_info=True)
        return {
            "symbol": symbol,
            "status": "failed",
            "error": str(exc),
            "elapsed_seconds": round(time.time() - start_time, 2),
        }


def get_stock_candles(
    db: Session,
    symbol: str,
    days: int = LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Get daily candles for a symbol from the database as a DataFrame."""
    cutoff = date.today() - timedelta(days=days)
    candles = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol,
        DailyCandle.date >= cutoff,
    ).order_by(DailyCandle.date).all()

    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "date": c.date,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
    } for c in candles])

    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df
