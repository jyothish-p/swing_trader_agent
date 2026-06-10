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
from typing import Optional

import requests

from app.config import NSE_SUFFIX

logger = logging.getLogger(__name__)

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

    for fetcher in (get_quote_tradingview, get_quote_nse, get_quote_yfinance):
        quote = fetcher(sym)
        if quote and quote.get("last_price") is not None:
            return quote

    return {"symbol": sym, "last_price": None, "source": "none"}
