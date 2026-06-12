"""Live market data helpers.

Quote priority:
1. TradingView scanner API
2. NSE quote API
3. yfinance fallback

Returns a unified dict with `last_price`, `change`, `change_pct`, `volume`
and `source`.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from app.config import CACHE_TTL_QUOTES, NSE_SUFFIX

logger = logging.getLogger(__name__)
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _to_float(v: Optional[object]) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace(",", "").replace("%", "")
        cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except Exception:
        return None


def _to_int(v: Optional[object]) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


def _cache_get(symbol: str) -> Optional[dict]:
    entry = _QUOTE_CACHE.get(symbol)
    if not entry:
        return None
    expires_at, payload = entry
    if time.time() >= expires_at:
        _QUOTE_CACHE.pop(symbol, None)
        return None
    return payload


def _cache_set(symbol: str, payload: dict) -> dict:
    _QUOTE_CACHE[symbol] = (time.time() + max(1, CACHE_TTL_QUOTES), payload)
    return payload


def get_quote_nse(symbol: str, timeout: int = 2) -> Optional[dict]:
    """Fetch live quote from NSE India public API."""
    sess = requests.Session()
    headers = {
        **_BROWSER_HEADERS,
        "Referer": "https://www.nseindia.com/market-data/live-equity-market",
        "Origin": "https://www.nseindia.com",
    }

    seed_urls = ["https://www.nseindia.com/market-data/live-equity-market"]

    try:
        for url in seed_urls:
            try:
                sess.get(url, headers=headers, timeout=timeout)
            except Exception:
                pass

        api_urls = [f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}"]

        for url in api_urls:
            r = sess.get(url, headers=headers, timeout=timeout)
            if r.status_code != 200:
                logger.debug("NSE quote HTTP %s for %s via %s", r.status_code, symbol, url)
                continue

            data = r.json()
            price = data.get("priceInfo") or {}
            security = data.get("securityWiseDP") or {}

            last = _to_float(price.get("lastPrice") or price.get("lastTradedPrice"))
            change = _to_float(price.get("change") or price.get("netChange"))
            change_pct = _to_float(price.get("pChange") or price.get("percentChange"))
            volume = _to_int(
                price.get("totalTradedVolume")
                or price.get("totalTradedVolumeRaw")
                or security.get("quantityTraded")
            )

            if last is None:
                continue

            return {
                "symbol": symbol.upper(),
                "last_price": last,
                "change": change,
                "change_pct": change_pct,
                "volume": volume,
                "source": "NSE",
            }
    except Exception as e:
        logger.debug("NSE quote failed for %s: %s", symbol, e)

    return None


def get_quote_tradingview(symbol: str, timeout: int = 8) -> Optional[dict]:
    """Fetch near-live quote from TradingView's India scanner."""
    try:
        payload = {
            "symbols": {"tickers": [f"NSE:{symbol.upper()}"], "query": {"types": []}},
            "columns": ["close", "change", "volume", "name", "type", "logoid"],
        }
        r = requests.post(
            "https://scanner.tradingview.com/india/scan",
            headers={
                **_BROWSER_HEADERS,
                "Content-Type": "application/json",
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
            },
            json=payload,
            timeout=timeout,
        )
        if r.status_code != 200:
            logger.debug("TradingView quote HTTP %s for %s", r.status_code, symbol)
            return None

        data = r.json().get("data") or []
        if not data:
            return None

        values = data[0].get("d") or []
        last = _to_float(values[0] if len(values) > 0 else None)
        change_pct = _to_float(values[1] if len(values) > 1 else None)
        volume = _to_int(values[2] if len(values) > 2 else None)

        change = None
        if last is not None and change_pct is not None:
            prev_close = last / (1 + (change_pct / 100)) if change_pct != -100 else None
            change = round(last - prev_close, 2) if prev_close not in (None, 0) else None

        if last is None:
            return None

        return {
            "symbol": symbol.upper(),
            "last_price": last,
            "change": change,
            "change_pct": change_pct,
            "volume": volume,
            "source": "TradingView",
        }
    except Exception as e:
        logger.debug("TradingView quote failed for %s: %s", symbol, e)
        return None


def get_quotes_tradingview_batch(symbols: list[str], timeout: int = 8) -> dict[str, dict]:
    """Fetch many NSE quotes in one TradingView scanner call."""
    tickers = [f"NSE:{symbol.upper()}" for symbol in symbols if symbol]
    if not tickers:
        return {}

    try:
        payload = {
            "symbols": {"tickers": tickers, "query": {"types": []}},
            "columns": ["close", "change", "volume"],
        }
        response = requests.post(
            "https://scanner.tradingview.com/india/scan",
            headers={
                **_BROWSER_HEADERS,
                "Content-Type": "application/json",
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.debug("TradingView batch quote HTTP %s for %s symbols", response.status_code, len(tickers))
            return {}

        rows = response.json().get("data") or []
        results: dict[str, dict] = {}
        for row in rows:
            symbol = row.get("s", "").split(":", 1)[-1].strip().upper()
            values = row.get("d") or []
            last = _to_float(values[0] if len(values) > 0 else None)
            change_pct = _to_float(values[1] if len(values) > 1 else None)
            volume = _to_int(values[2] if len(values) > 2 else None)

            if last is None or not symbol:
                continue

            change = None
            if change_pct is not None:
                prev_close = last / (1 + (change_pct / 100)) if change_pct != -100 else None
                change = round(last - prev_close, 2) if prev_close not in (None, 0) else None

            results[symbol] = _cache_set(symbol, {
                "symbol": symbol,
                "last_price": last,
                "change": change,
                "change_pct": change_pct,
                "volume": volume,
                "source": "TradingView",
            })
        return results
    except Exception as exc:
        logger.debug("TradingView batch quote failed for %s symbols: %s", len(tickers), exc)
        return {}


def get_quote_yfinance(symbol: str) -> Optional[dict]:
    """Fallback using yfinance to get a recent price snapshot."""
    try:
        import yfinance as yf

        yf_symbol = f"{symbol}{NSE_SUFFIX}"
        t = yf.Ticker(yf_symbol)

        hist = t.history(period="1d", interval="1m")
        if hist is None or hist.empty:
            fast = getattr(t, "fast_info", None) or {}
            last = _to_float(fast.get("last_price") or fast.get("last_trade_price"))
            vol = _to_int(fast.get("last_volume") or fast.get("volume"))
            return {
                "symbol": symbol.upper(),
                "last_price": last,
                "change": None,
                "change_pct": None,
                "volume": vol,
                "source": "YFinance",
            }

        last_row = hist.iloc[-1]
        last = _to_float(last_row.get("Close"))
        volume = _to_int(last_row.get("Volume"))
        prev_close = _to_float(hist.iloc[-2].get("Close")) if len(hist) >= 2 else None
        change = round(last - prev_close, 2) if last is not None and prev_close is not None else None
        change_pct = round((change / prev_close) * 100, 2) if change is not None and prev_close not in (None, 0) else None

        return {
            "symbol": symbol.upper(),
            "last_price": last,
            "change": change,
            "change_pct": change_pct,
            "volume": volume,
            "source": "YFinance",
        }
    except Exception as e:
        logger.debug("YFinance quote failed for %s: %s", symbol, e)
        return None


def get_live_quote(symbol: str) -> dict:
    """Unified live quote lookup."""
    sym = symbol.upper()
    cached = _cache_get(sym)
    if cached:
        return cached

    for fetcher in (get_quote_tradingview, get_quote_nse, get_quote_yfinance):
        quote = fetcher(sym)
        if quote and quote.get("last_price") is not None:
            return _cache_set(sym, quote)

    return {"symbol": sym, "last_price": None, "source": "none"}


def get_live_quotes_batch(symbols: list[str]) -> dict[str, dict]:
    """Batch quote lookup with TradingView-first fan-in and cached fallbacks."""
    normalized = [symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()]
    if not normalized:
        return {}

    results: dict[str, dict] = {}
    missing: list[str] = []

    for symbol in normalized:
        cached = _cache_get(symbol)
        if cached:
            results[symbol] = cached
        else:
            missing.append(symbol)

    if missing:
        results.update(get_quotes_tradingview_batch(missing))

    still_missing = [symbol for symbol in normalized if symbol not in results]
    for symbol in still_missing:
        results[symbol] = get_live_quote(symbol)

    return results
