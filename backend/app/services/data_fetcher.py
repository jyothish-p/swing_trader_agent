"""
Bulk Historical Data Fetcher.
Downloads 1 year of daily OHLCV for all stocks in one fast batch via yfinance.
Also handles incremental updates (only fetch missing days).
"""
import logging
from collections import defaultdict
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import DailyCandle
from app.config import LOOKBACK_DAYS, NSE_SUFFIX

logger = logging.getLogger(__name__)


def _yf_symbol(symbol: str) -> str:
    """Convert NSE symbol to yfinance format."""
    # Handle special characters
    s = symbol.replace("&", "%26") if "&" in symbol else symbol
    return f"{s}{NSE_SUFFIX}"


def _get_last_date_in_db(db: Session, symbol: str) -> date | None:
    """Get the most recent date we have data for this symbol."""
    result = db.query(func.max(DailyCandle.date)).filter(
        DailyCandle.symbol == symbol
    ).scalar()
    return result


def _get_existing_dates_map(
    db: Session,
    symbols: list[str],
    start_date: date,
) -> dict[str, set[date]]:
    """Fetch existing candle dates for a symbol group in one query."""
    existing_dates: dict[str, set[date]] = defaultdict(set)
    rows = db.query(DailyCandle.symbol, DailyCandle.date).filter(
        DailyCandle.symbol.in_(symbols),
        DailyCandle.date >= start_date,
    ).all()
    for symbol, candle_date in rows:
        existing_dates[symbol].add(candle_date)
    return existing_dates


def bulk_download_historical(
    symbols: list[str],
    db: Session,
    full_refresh: bool = False
) -> dict:
    """
    Download historical data for all symbols in bulk.
    Uses incremental updates by default (only fetches missing days).

    Returns: {
        'success': ['SYM1', 'SYM2', ...],
        'failed': ['SYM3', ...],
        'skipped': ['SYM4', ...],  # already up to date
        'total_candles': 12345,
        'elapsed_seconds': 45.2,
    }
    """
    import time
    start_time = time.time()

    result = {"success": [], "failed": [], "skipped": [], "total_candles": 0}
    today = date.today()
    start_date = today - timedelta(days=LOOKBACK_DAYS)

    # Determine what each symbol needs
    symbols_to_fetch = {}
    for sym in symbols:
        if full_refresh:
            symbols_to_fetch[sym] = start_date
        else:
            last_date = _get_last_date_in_db(db, sym)
            if last_date and last_date >= today - timedelta(days=1):
                result["skipped"].append(sym)
                continue
            elif last_date:
                # Fetch from day after last date
                symbols_to_fetch[sym] = last_date + timedelta(days=1)
            else:
                symbols_to_fetch[sym] = start_date

    if not symbols_to_fetch:
        logger.info("All symbols up to date, nothing to fetch")
        result["elapsed_seconds"] = round(time.time() - start_time, 2)
        return result

    logger.info(f"Fetching data for {len(symbols_to_fetch)} symbols...")

    # Group by start date to minimize API calls
    # Most will share the same start date
    date_groups: dict[date, list[str]] = {}
    for sym, sd in symbols_to_fetch.items():
        date_groups.setdefault(sd, []).append(sym)

    for fetch_start, group_symbols in date_groups.items():
        yf_symbols = [_yf_symbol(s) for s in group_symbols]
        yf_str = " ".join(yf_symbols)
        existing_dates_map = (
            {} if full_refresh
            else _get_existing_dates_map(db, group_symbols, fetch_start - timedelta(days=7))
        )

        try:
            logger.info(
                f"yfinance batch: {len(group_symbols)} stocks "
                f"from {fetch_start} to {today}"
            )
            data = yf.download(
                yf_str,
                start=fetch_start.isoformat(),
                end=(today + timedelta(days=1)).isoformat(),
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )

            if data.empty:
                logger.warning("yfinance returned empty dataframe")
                result["failed"].extend(group_symbols)
                continue

            if full_refresh:
                db.query(DailyCandle).filter(
                    DailyCandle.symbol.in_(group_symbols),
                    DailyCandle.date >= fetch_start,
                ).delete(synchronize_session=False)
                db.commit()

            pending_objects = []

            # Process each symbol
            for sym in group_symbols:
                yf_sym = _yf_symbol(sym)
                try:
                    if len(group_symbols) == 1:
                        # Single stock — yfinance may still use MultiIndex columns
                        df = data.copy()
                        if isinstance(df.columns, pd.MultiIndex):
                            # Try extracting this ticker's columns from MultiIndex
                            if yf_sym in df.columns.get_level_values(0):
                                df = df[yf_sym].copy()
                            else:
                                # Sometimes yfinance drops the .NS suffix in columns
                                df.columns = df.columns.droplevel(0)
                    else:
                        if isinstance(data.columns, pd.MultiIndex) and yf_sym in data.columns.get_level_values(0):
                            df = data[yf_sym].copy()
                        else:
                            df = pd.DataFrame()

                    # Flatten any remaining MultiIndex columns
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(-1)

                    if df.empty or df.dropna(how="all").empty:
                        result["failed"].append(sym)
                        continue

                    df = df.dropna(subset=["Close"])
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

                except Exception as e:
                    logger.warning(f"Failed to process {sym}: {e}", exc_info=True)
                    result["failed"].append(sym)

            try:
                if pending_objects:
                    db.bulk_save_objects(pending_objects)
                db.commit()
            except IntegrityError as e:
                db.rollback()
                logger.warning(f"Duplicate candle conflict in batch {group_symbols}: {e}")
                result["failed"].extend([s for s in group_symbols if s not in result["failed"]])

        except Exception as e:
            db.rollback()
            logger.error(f"yfinance batch download failed: {e}")
            result["failed"].extend(group_symbols)

    result["elapsed_seconds"] = round(time.time() - start_time, 2)
    logger.info(
        f"Data fetch complete: {len(result['success'])} success, "
        f"{len(result['failed'])} failed, {len(result['skipped'])} skipped, "
        f"{result['total_candles']} candles in {result['elapsed_seconds']}s"
    )
    return result


def get_stock_candles(
    db: Session,
    symbol: str,
    days: int = LOOKBACK_DAYS
) -> pd.DataFrame:
    """
    Get daily candles for a symbol from the database as a DataFrame.
    """
    cutoff = date.today() - timedelta(days=days)
    candles = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol,
        DailyCandle.date >= cutoff
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
