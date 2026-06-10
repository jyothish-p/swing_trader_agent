"""Live market context helpers for TITAN v19.

These helpers fetch lightweight live context that the v19 document expects:
- sector / industry
- mapped NSE sector index or closest proxy
- sector weekly RSI + weekly structure
- top sector peers with 1M performance and breakout note
- NIFTY mood / weekly RSI
- minimal news tone

If any input cannot be fetched reliably, callers should treat it as
`DATA NOT PROVIDED` with a score of 0.
"""
from __future__ import annotations

import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from sqlalchemy.orm import Session

from app.models import DeliveryData, Stock

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_PROFILE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SECTOR_PEER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_INDEX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NEWS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

_PROFILE_TTL = 60 * 30
_SECTOR_TTL = 60 * 30
_INDEX_TTL = 60 * 20
_NEWS_TTL = 60 * 45

DATA_NOT_PROVIDED = "DATA NOT PROVIDED"


def _cache_get(cache: dict[str, tuple[float, dict[str, Any]]], key: str) -> dict[str, Any] | None:
    entry = cache.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.time() >= expires_at:
        cache.pop(key, None)
        return None
    return value


def _cache_set(
    cache: dict[str, tuple[float, dict[str, Any]]],
    key: str,
    value: dict[str, Any],
    ttl: int,
) -> dict[str, Any]:
    cache[key] = (time.time() + ttl, value)
    return value


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _tv_scan(payload: dict[str, Any], timeout: int = 12) -> dict[str, Any] | None:
    try:
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
            logger.debug("TradingView scan HTTP %s", response.status_code)
            return None
        return response.json()
    except Exception as exc:
        logger.debug("TradingView scan failed: %s", exc)
        return None


def _yahoo_chart(symbol: str, range_: str = "6mo", interval: str = "1wk") -> dict[str, Any] | None:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval={interval}"
        response = requests.get(url, headers=_BROWSER_HEADERS, timeout=12)
        if response.status_code != 200:
            logger.debug("Yahoo chart HTTP %s for %s", response.status_code, symbol)
            return None
        payload = response.json()
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        return result
    except Exception as exc:
        logger.debug("Yahoo chart failed for %s: %s", symbol, exc)
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, (float, int)):
            if math.isnan(value) or math.isinf(value):
                return None
            return float(value)
        text = str(value).replace(",", "").replace("%", "").strip()
        return float(text) if text else None
    except Exception:
        return None


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(closes)):
        delta = closes[idx] - closes[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    for idx in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _detect_structure(prices: list[float], period: int = 24) -> tuple[str, str]:
    p = min(period, len(prices))
    segment = prices[-p:]
    if len(segment) < 8:
        return "range", "neutral"

    swing_highs: list[float] = []
    swing_lows: list[float] = []
    window = 2
    for idx in range(window, len(segment) - window):
        centre = segment[idx]
        slice_ = segment[idx - window: idx + window + 1]
        if centre == max(slice_):
            swing_highs.append(centre)
        if centre == min(slice_):
            swing_lows.append(centre)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        first = segment[0]
        last = segment[-1]
        if first and last > first * 1.05:
            return "HH/HL", "bullish"
        if first and last < first * 0.95:
            return "LH/LL", "bearish"
        return "range", "neutral"

    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]

    if hh and hl:
        return "HH/HL", "bullish"
    if lh and ll:
        return "LH/LL", "bearish"
    return "range", "neutral"


def _trend_state(structure: str, weekly_rsi: float | None) -> str:
    if weekly_rsi is None:
        return DATA_NOT_PROVIDED
    if structure == "HH/HL" and weekly_rsi >= 55:
        return "bullish"
    if structure == "LH/LL" and weekly_rsi <= 45:
        return "bearish"
    return "range"


def _mood_from_context(structure: str, weekly_rsi: float | None) -> str:
    if weekly_rsi is None:
        return DATA_NOT_PROVIDED
    if structure == "HH/HL" and weekly_rsi >= 55:
        return "Positive"
    if structure == "LH/LL" and weekly_rsi <= 45:
        return "Negative"
    return "Neutral"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _normalize(value: float | None, lower: float, upper: float, fallback: float = 0.0) -> float:
    if value is None or upper <= lower:
        return fallback
    return _clamp((value - lower) / (upper - lower), 0.0, 1.0)


def _score_sector_momentum(
    *,
    sector_rsi: float | None,
    sector_structure: str,
    sector_perf_1m: float | None,
    sector_perf_3m: float | None,
    sector_perf_6m: float | None,
    peer_positive: int,
    peer_avg_perf: float | None,
    peer_breakouts: int,
) -> int:
    has_any_signal = any(
        value is not None
        for value in (sector_rsi, sector_perf_1m, sector_perf_3m, sector_perf_6m, peer_avg_perf)
    ) or peer_positive > 0 or peer_breakouts > 0
    if not has_any_signal:
        return 0

    rsi_component = 2.5 * _normalize(sector_rsi, 30.0, 65.0, fallback=0.15)
    structure_component = {
        "HH/HL": 1.75,
        "range": 1.0,
        "LH/LL": 0.25,
    }.get(sector_structure, 0.75)

    perf_component = 2.5 * (
        0.50 * _normalize(sector_perf_1m, -8.0, 8.0, fallback=0.25)
        + 0.35 * _normalize(sector_perf_3m, -15.0, 15.0, fallback=0.25)
        + 0.15 * _normalize(sector_perf_6m, -25.0, 25.0, fallback=0.25)
    )

    breadth_component = 2.25 * (
        0.60 * _clamp(peer_positive / 3.0, 0.0, 1.0)
        + 0.40 * _normalize(peer_avg_perf, -5.0, 25.0, fallback=0.0)
    )

    breakout_component = 1.0 * _clamp(peer_breakouts / 2.0, 0.0, 1.0)

    recovery_bonus = 0.0
    if sector_rsi is not None and sector_rsi < 45:
        if peer_positive >= 3 and (peer_avg_perf or -999) >= 10 and (sector_perf_1m or -999) > -4:
            recovery_bonus += 1.0
        elif peer_positive >= 2 and (peer_avg_perf or -999) >= 5 and (sector_perf_3m or -999) > -8:
            recovery_bonus += 0.5

    momentum_bonus = 0.0
    if sector_structure == "HH/HL" and (sector_rsi or 0) >= 58 and (sector_perf_1m or 0) > 2:
        momentum_bonus += 0.5

    penalty = 0.0
    if (
        sector_structure == "LH/LL"
        and (sector_rsi or 100) < 40
        and (sector_perf_1m or 0) < -4
        and (sector_perf_3m or 0) < -8
    ):
        penalty += 0.75

    raw_score = (
        rsi_component
        + structure_component
        + perf_component
        + breadth_component
        + breakout_component
        + recovery_bonus
        + momentum_bonus
        - penalty
    )
    return int(round(_clamp(raw_score, 0.0, 10.0)))


def preload_stock_profiles(db: Session, symbols: list[str], chunk_size: int = 60) -> None:
    """Warm TradingView profile cache in chunks for fast batch analysis."""
    missing = [symbol.upper() for symbol in symbols if not _cache_get(_PROFILE_CACHE, symbol.upper())]
    if not missing:
        return

    for start in range(0, len(missing), chunk_size):
        chunk = missing[start:start + chunk_size]
        payload = {
            "symbols": {"tickers": [f"NSE:{symbol}" for symbol in chunk], "query": {"types": []}},
            "columns": ["name", "description", "sector", "industry", "Perf.1M", "RSI|1W"],
        }
        data = _tv_scan(payload)
        if not data:
            continue
        for entry in data.get("data") or []:
            full_symbol = entry.get("s", "")
            if ":" not in full_symbol:
                continue
            symbol = full_symbol.split(":", 1)[1].upper()
            values = entry.get("d") or []
            profile = {
                "symbol": symbol,
                "name": values[0] if len(values) > 0 else symbol,
                "description": values[1] if len(values) > 1 else "",
                "sector": values[2] if len(values) > 2 else "",
                "industry": values[3] if len(values) > 3 else "",
                "perf_1m": _safe_float(values[4] if len(values) > 4 else None),
                "weekly_rsi": _safe_float(values[5] if len(values) > 5 else None),
            }
            _cache_set(_PROFILE_CACHE, symbol, profile, _PROFILE_TTL)

            stock = db.query(Stock).filter(Stock.symbol == symbol).first()
            if stock:
                if profile["description"] and not stock.name:
                    stock.name = profile["description"]
                if profile["sector"]:
                    stock.sector = profile["sector"]
                if profile["industry"]:
                    stock.industry = profile["industry"]
        try:
            db.commit()
        except Exception:
            db.rollback()


def get_stock_profile(db: Session, symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    cached = _cache_get(_PROFILE_CACHE, symbol)
    if cached:
        return cached

    preload_stock_profiles(db, [symbol], chunk_size=1)
    cached = _cache_get(_PROFILE_CACHE, symbol)
    if cached:
        return cached

    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    profile = {
        "symbol": symbol,
        "name": stock.name if stock else symbol,
        "description": stock.name if stock else symbol,
        "sector": stock.sector if stock and stock.sector else "",
        "industry": stock.industry if stock and stock.industry else "",
        "perf_1m": None,
        "weekly_rsi": None,
    }
    return _cache_set(_PROFILE_CACHE, symbol, profile, _PROFILE_TTL)


def _sector_proxy_for(sector: str, industry: str) -> dict[str, str]:
    search_text = " ".join(part for part in ((sector or "").lower(), (industry or "").lower()) if part).strip()

    mapping = [
        (("bank", "financial", "finance", "insurance", "investment"), {"yahoo": "^NSEBANK", "tv": "NSE:NIFTY BANK", "label": "Nifty Bank"}),
        (("pharma", "pharmaceutical", "pharmaceuticals", "health technology", "biotech", "drug"), {"yahoo": "CNXPHARMA.NS", "tv": "NSE:CNXPHARMA", "label": "Nifty Pharma"}),
        (("metal", "mining", "steel", "non-energy minerals"), {"yahoo": "CNXMETAL.NS", "tv": "NSE:CNXMETAL", "label": "Nifty Metal"}),
        (("energy minerals", "oil", "gas", "utilities", "refining", "petrochem"), {"yahoo": "CNXENERGY.NS", "tv": "NSE:CNXENERGY", "label": "Nifty Energy"}),
        (("auto", "vehicle", "tyre", "transportation"), {"yahoo": "CNXAUTO.NS", "tv": "NSE:CNXAUTO", "label": "Nifty Auto"}),
        (("consumer", "retail", "food", "fmcg", "household"), {"yahoo": "CNXFMCG.NS", "tv": "NSE:CNXFMCG", "label": "Nifty FMCG"}),
        (("industrial", "capital goods", "construction", "engineering", "infrastructure"), {"yahoo": "CNXINFRA.NS", "tv": "NSE:CNXINFRA", "label": "Nifty Infra"}),
        (("real estate", "realty"), {"yahoo": "CNXREALTY.NS", "tv": "NSE:CNXREALTY", "label": "Nifty Realty"}),
        (("media", "entertainment", "broadcast"), {"yahoo": "CNXMEDIA.NS", "tv": "NSE:CNXMEDIA", "label": "Nifty Media"}),
        (("technology services", "information technology", "software", "communication equipment", "telecommunication", "electronic technology", "semiconductor"), {"yahoo": "CNXIT.NS", "tv": "NSE:CNXIT", "label": "Nifty IT"}),
    ]

    for keywords, proxy in mapping:
        if any(keyword in search_text for keyword in keywords):
            return proxy

    return {"yahoo": "^NSEI", "tv": "NSE:NIFTY", "label": "Nifty 50"}


def get_index_context(yahoo_symbol: str, label: str, tv_symbol: str | None = None) -> dict[str, Any]:
    cache_key = f"{yahoo_symbol}|{tv_symbol or ''}|{label}"
    cached = _cache_get(_INDEX_CACHE, cache_key)
    if cached:
        return cached

    weekly_rsi_from_tv = None
    perf_1m_from_tv = None
    perf_3m_from_tv = None
    perf_6m_from_tv = None
    if tv_symbol:
        tv_data = _tv_scan({
            "symbols": {"tickers": [tv_symbol], "query": {"types": []}},
            "columns": ["name", "description", "RSI|1W", "Perf.1M", "Perf.3M", "Perf.6M", "close", "type"],
        })
        try:
            row = ((tv_data or {}).get("data") or [None])[0]
            values = (row or {}).get("d") or []
            weekly_rsi_from_tv = _safe_float(values[2] if len(values) > 2 else None)
            perf_1m_from_tv = _safe_float(values[3] if len(values) > 3 else None)
            perf_3m_from_tv = _safe_float(values[4] if len(values) > 4 else None)
            perf_6m_from_tv = _safe_float(values[5] if len(values) > 5 else None)
        except Exception:
            weekly_rsi_from_tv = None
            perf_1m_from_tv = None
            perf_3m_from_tv = None
            perf_6m_from_tv = None

    def _fallback_structure_from_tv() -> str:
        if weekly_rsi_from_tv is None:
            return DATA_NOT_PROVIDED
        if perf_1m_from_tv is None and perf_3m_from_tv is None and perf_6m_from_tv is None:
            return DATA_NOT_PROVIDED
        p1 = perf_1m_from_tv or 0
        p3 = perf_3m_from_tv or 0
        p6 = perf_6m_from_tv or 0
        if weekly_rsi_from_tv >= 55 and p1 >= 0 and p3 >= 0 and p6 >= -2:
            return "HH/HL"
        if weekly_rsi_from_tv <= 45 and p1 <= 0 and p3 <= 0 and p6 <= 2:
            return "LH/LL"
        return "range"

    result = _yahoo_chart(yahoo_symbol, range_="6mo", interval="1wk")
    if not result:
        structure = _fallback_structure_from_tv()
        context = {
            "label": label,
            "symbol": yahoo_symbol,
            "weekly_rsi": weekly_rsi_from_tv,
            "structure": structure,
            "trend_state": _trend_state(structure, weekly_rsi_from_tv),
            "mood": _mood_from_context(structure, weekly_rsi_from_tv),
            "perf_1m": perf_1m_from_tv,
            "perf_3m": perf_3m_from_tv,
            "perf_6m": perf_6m_from_tv,
        }
        return _cache_set(_INDEX_CACHE, cache_key, context, _INDEX_TTL)

    closes = [
        _safe_float(close)
        for close in (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        if _safe_float(close) is not None
    ]
    if len(closes) < 5:
        structure = _fallback_structure_from_tv()
        context = {
            "label": label,
            "symbol": yahoo_symbol,
            "weekly_rsi": weekly_rsi_from_tv,
            "structure": structure,
            "trend_state": _trend_state(structure, weekly_rsi_from_tv),
            "mood": _mood_from_context(structure, weekly_rsi_from_tv),
            "perf_1m": perf_1m_from_tv,
            "perf_3m": perf_3m_from_tv,
            "perf_6m": perf_6m_from_tv,
        }
        return _cache_set(_INDEX_CACHE, cache_key, context, _INDEX_TTL)

    weekly_rsi = weekly_rsi_from_tv if weekly_rsi_from_tv is not None else _calc_rsi(closes, 14)
    structure, _ = _detect_structure(closes, period=24)
    perf_1m = None
    if len(closes) >= 5 and closes[-5] not in (None, 0):
        perf_1m = round((closes[-1] / closes[-5] - 1) * 100, 2)
    perf_3m = None
    if len(closes) >= 13 and closes[-13] not in (None, 0):
        perf_3m = round((closes[-1] / closes[-13] - 1) * 100, 2)
    perf_6m = None
    if len(closes) >= 24 and closes[-24] not in (None, 0):
        perf_6m = round((closes[-1] / closes[-24] - 1) * 100, 2)

    context = {
        "label": label,
        "symbol": yahoo_symbol,
        "weekly_rsi": weekly_rsi,
        "structure": structure,
        "trend_state": _trend_state(structure, weekly_rsi),
        "mood": _mood_from_context(structure, weekly_rsi),
        "perf_1m": perf_1m,
        "perf_3m": perf_3m,
        "perf_6m": perf_6m,
    }
    return _cache_set(_INDEX_CACHE, cache_key, context, _INDEX_TTL)


def get_sector_peers(symbol: str, sector: str, limit: int = 3) -> dict[str, Any]:
    if not sector:
        return {"peers": [], "breakout_count": 0, "positive_count": 0, "avg_perf_1m": None}

    cached = _cache_get(_SECTOR_PEER_CACHE, sector)
    if cached:
        peers = [peer for peer in cached.get("peers", []) if peer.get("symbol") != symbol.upper()]
        return {
            "peers": peers[:limit],
            "breakout_count": sum(1 for peer in peers[:limit] if peer.get("breakout")),
            "positive_count": sum(1 for peer in peers[:limit] if (peer.get("perf_1m") or 0) > 0),
            "avg_perf_1m": round(sum((peer.get("perf_1m") or 0) for peer in peers[:limit]) / len(peers[:limit]), 2) if peers[:limit] else None,
        }

    payload = {
        "filter": [{"left": "sector", "operation": "equal", "right": sector}],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "sector", "Perf.1M", "RSI|1W", "High.1M", "close"],
        "sort": {"sortBy": "Perf.1M", "sortOrder": "desc"},
        "range": [0, 30],
    }
    data = _tv_scan(payload)
    peers: list[dict[str, Any]] = []
    if data:
        seen: set[str] = set()
        for entry in data.get("data") or []:
            full_symbol = entry.get("s", "")
            if not full_symbol.startswith("NSE:"):
                continue
            peer_symbol = full_symbol.split(":", 1)[1].upper()
            if peer_symbol in seen:
                continue
            seen.add(peer_symbol)
            values = entry.get("d") or []
            perf_1m = _safe_float(values[2] if len(values) > 2 else None)
            high_1m = _safe_float(values[4] if len(values) > 4 else None)
            close = _safe_float(values[5] if len(values) > 5 else None)
            peers.append({
                "symbol": peer_symbol,
                "name": values[0] if len(values) > 0 else peer_symbol,
                "perf_1m": perf_1m,
                "weekly_rsi": _safe_float(values[3] if len(values) > 3 else None),
                "breakout": bool(close and high_1m and close >= high_1m * 0.99),
            })

    cached_payload = {"peers": peers}
    _cache_set(_SECTOR_PEER_CACHE, sector, cached_payload, _SECTOR_TTL)
    filtered = [peer for peer in peers if peer.get("symbol") != symbol.upper()]
    top = filtered[:limit]
    return {
        "peers": top,
        "breakout_count": sum(1 for peer in top if peer.get("breakout")),
        "positive_count": sum(1 for peer in top if (peer.get("perf_1m") or 0) > 0),
        "avg_perf_1m": round(sum((peer.get("perf_1m") or 0) for peer in top) / len(top), 2) if top else None,
    }


def get_delivery_context(db: Session, symbol: str) -> dict[str, Any]:
    rows = db.query(DeliveryData).filter(
        DeliveryData.symbol == symbol.upper()
    ).order_by(DeliveryData.date.desc()).limit(10).all()

    if not rows:
        return {"avg_pct": None, "trend": DATA_NOT_PROVIDED}

    ordered = list(reversed(rows))
    delivery_values = [row.delivery_pct for row in ordered if row.delivery_pct is not None]
    if not delivery_values:
        return {"avg_pct": None, "trend": DATA_NOT_PROVIDED}

    avg_pct = round(sum(delivery_values) / len(delivery_values), 2)
    if len(delivery_values) >= 6:
        earlier = sum(delivery_values[: len(delivery_values) // 2]) / (len(delivery_values) // 2)
        later = sum(delivery_values[len(delivery_values) // 2:]) / (len(delivery_values) - len(delivery_values) // 2)
        if later > earlier + 1:
            trend = "rising"
        elif later < earlier - 1:
            trend = "falling"
        else:
            trend = "stable"
    else:
        trend = "stable"
    return {"avg_pct": avg_pct, "trend": trend}


def _classify_news_tone_from_titles(titles: list[str]) -> str:
    if not titles:
        return DATA_NOT_PROVIDED

    positive_words = {
        "surge", "beat", "beats", "growth", "approval", "win", "wins", "record", "strong",
        "order", "orders", "rebound", "upgrade", "expands", "rise", "rises", "bullish",
    }
    negative_words = {
        "fall", "falls", "loss", "losses", "probe", "fraud", "penalty", "downgrade", "weak",
        "delay", "miss", "misses", "cuts", "drop", "drops", "lawsuit", "warning", "bearish",
    }

    score = 0
    for title in titles:
        lower = title.lower()
        score += sum(1 for word in positive_words if word in lower)
        score -= sum(1 for word in negative_words if word in lower)

    if score > 1:
        return "Positive"
    if score < -1:
        return "Negative"
    return "Neutral"


def _fetch_google_news_titles(query: str, lookback_days: int = 14, max_titles: int = 8) -> list[str]:
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    response = requests.get(url, headers=_BROWSER_HEADERS, timeout=10)
    if response.status_code != 200:
        return []

    root = ET.fromstring(response.text)
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    titles: list[str] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title:
            continue
        try:
            published = parsedate_to_datetime(pub_date)
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            if published < cutoff:
                continue
        except Exception:
            pass
        titles.append(title)
        if len(titles) >= max_titles:
            break
    return titles


def build_news_queries(company_name: str, symbol: str) -> list[str]:
    company_name = (company_name or symbol).strip()
    symbol = symbol.upper().strip()
    aliases: list[str] = [company_name]

    without_parens = re.sub(r"\s*\([^)]*\)", "", company_name).strip()
    if without_parens:
        aliases.append(without_parens)

    trailing_suffix_pattern = re.compile(
        r"\b(limited|ltd|ltd\.|inc|inc\.|corp|corp\.|corporation|co|co\.|company)\b\.?$",
        re.IGNORECASE,
    )
    trimmed = without_parens or company_name
    while True:
        updated = trailing_suffix_pattern.sub("", trimmed).strip(" ,.-")
        if updated == trimmed or not updated:
            break
        trimmed = updated
    aliases.append(trimmed)

    compact = re.sub(r"\s+", " ", trimmed).strip()
    parts = compact.split()
    if len(parts) >= 2:
        aliases.append(" ".join(parts[:2]))
    if len(parts) >= 3:
        aliases.append(" ".join(parts[:3]))

    aliases = _unique_preserve_order([alias for alias in aliases if alias])
    queries: list[str] = []
    for alias in aliases:
        queries.extend([
            f"\"{alias}\" NSE",
            f"\"{alias}\" stock",
            f"\"{alias}\" share",
            f"\"{alias}\" India",
        ])
    queries.extend([
        f"\"{symbol}\" NSE stock",
        f"\"{symbol}\" share",
    ])
    return _unique_preserve_order(queries)


def get_news_tone(query: str, lookback_days: int = 14, fallback_queries: list[str] | None = None) -> dict[str, Any]:
    cache_key = query.lower().strip()
    cached = _cache_get(_NEWS_CACHE, cache_key)
    if cached and (cached.get("tone") != DATA_NOT_PROVIDED or not fallback_queries):
        return cached

    try:
        titles: list[str] = []
        queries = _unique_preserve_order([query, *(fallback_queries or [])])
        for candidate in queries:
            titles.extend(_fetch_google_news_titles(candidate, lookback_days=lookback_days, max_titles=5))
            titles = _unique_preserve_order(titles)
            if len(titles) >= 8:
                break

        tone = {
            "tone": _classify_news_tone_from_titles(titles),
            "titles": titles[:5],
        }
        return _cache_set(_NEWS_CACHE, cache_key, tone, _NEWS_TTL)
    except Exception as exc:
        logger.debug("News tone fetch failed for %s: %s", query, exc)
        tone = {"tone": DATA_NOT_PROVIDED, "titles": []}
        return _cache_set(_NEWS_CACHE, cache_key, tone, _NEWS_TTL)


def preload_news_tones(db: Session, symbols: list[str], max_workers: int = 8) -> None:
    """Warm Google News tone cache for many symbols concurrently."""
    query_jobs: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for symbol in symbols:
        profile = get_stock_profile(db, symbol.upper())
        query_name = profile.get("description") or profile.get("name") or symbol.upper()
        queries = build_news_queries(query_name, symbol.upper())
        if not queries:
            continue
        query = queries[0]
        cache_key = query.lower().strip()
        cached = _cache_get(_NEWS_CACHE, cache_key)
        if cache_key in seen or (cached and cached.get("tone") != DATA_NOT_PROVIDED):
            continue
        seen.add(cache_key)
        query_jobs.append((query, queries[1:]))

    if not query_jobs:
        return

    workers = max(1, min(max_workers, len(query_jobs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(lambda job: get_news_tone(job[0], fallback_queries=job[1]), query_jobs))


def build_market_context(
    db: Session,
    symbol: str,
    *,
    cmp: float,
    trigger: float,
    rsi: float | None,
    momentum_1m: float,
    weekly_structure: str,
    mode: str = "full",
) -> dict[str, Any]:
    symbol = symbol.upper()
    profile = get_stock_profile(db, symbol)
    sector = profile.get("sector") or ""
    industry = profile.get("industry") or ""

    proxy = _sector_proxy_for(sector, industry)
    sector_index = get_index_context(proxy["yahoo"], proxy["label"], proxy.get("tv"))
    nifty = get_index_context("^NSEI", "Nifty 50", "NSE:NIFTY")
    peers = get_sector_peers(symbol, sector) if sector else {"peers": [], "breakout_count": 0, "positive_count": 0, "avg_perf_1m": None}
    delivery = get_delivery_context(db, symbol)

    peer_breakouts = peers.get("breakout_count", 0)
    peer_positive = peers.get("positive_count", 0)
    peer_avg_perf = peers.get("avg_perf_1m")
    sector_rsi = sector_index.get("weekly_rsi")
    sector_structure = sector_index.get("structure")
    sector_perf_1m = sector_index.get("perf_1m")
    sector_perf_3m = sector_index.get("perf_3m")
    sector_perf_6m = sector_index.get("perf_6m")

    if sector_structure == DATA_NOT_PROVIDED:
        if sector_rsi is not None:
            if sector_rsi >= 55 and (sector_perf_1m or 0) >= 0 and (sector_perf_3m or 0) >= 0:
                sector_structure = "HH/HL"
            elif sector_rsi <= 45 and (sector_perf_1m or 0) <= 0 and (sector_perf_3m or 0) <= 0:
                sector_structure = "LH/LL"
            else:
                sector_structure = "range"

    sector_momentum_score = _score_sector_momentum(
        sector_rsi=sector_rsi,
        sector_structure=sector_structure,
        sector_perf_1m=sector_perf_1m,
        sector_perf_3m=sector_perf_3m,
        sector_perf_6m=sector_perf_6m,
        peer_positive=peer_positive,
        peer_avg_perf=peer_avg_perf,
        peer_breakouts=peer_breakouts,
    )

    news_tone = DATA_NOT_PROVIDED
    news_titles: list[str] = []
    if mode in {"full", "batch"}:
        query_name = profile.get("description") or profile.get("name") or symbol
        queries = build_news_queries(query_name, symbol)
        news = get_news_tone(queries[0], fallback_queries=queries[1:])
        news_tone = news["tone"]
        news_titles = news["titles"]

    extension_pct = round((cmp - trigger) / trigger * 100, 2) if trigger else 0
    if extension_pct > 3 or (rsi or 50) >= 70 or momentum_1m >= 15:
        retail_psych = "FOMO"
    elif weekly_structure == "LH/LL" or (rsi or 50) <= 40 or momentum_1m <= -8:
        retail_psych = "Fear"
    else:
        retail_psych = "Neutral"

    sentiment_component_scores = {
        "news_tone": 100 if news_tone == "Positive" else 50 if news_tone == "Neutral" else 0,
        "sector_mood": 100 if sector_index["mood"] == "Positive" else 50 if sector_index["mood"] == "Neutral" else 0,
        "nifty_mood": 100 if nifty["mood"] == "Positive" else 50 if nifty["mood"] == "Neutral" else 0,
        "retail_psych": 50 if retail_psych == "Neutral" else 20,
    }
    sentiment_score = round(sum(sentiment_component_scores.values()) / len(sentiment_component_scores))

    missing_data: list[str] = []
    if not sector:
        missing_data.append("sector")
    if sector_index["weekly_rsi"] is None:
        missing_data.append("sector_index")
    if news_tone == DATA_NOT_PROVIDED:
        missing_data.append("news_tone")
    if delivery["avg_pct"] is None:
        missing_data.append("delivery_pct")
    missing_data.append("corporate_actions")

    return {
        "name": profile.get("description") or profile.get("name") or symbol,
        "sector": sector or DATA_NOT_PROVIDED,
        "industry": industry or DATA_NOT_PROVIDED,
        "sector_index": sector_index["label"],
        "sector_index_symbol": sector_index["symbol"],
        "sector_weekly_rsi": sector_index["weekly_rsi"],
        "sector_structure": sector_structure,
        "sector_trend_state": sector_index["trend_state"],
        "sector_mood": sector_index["mood"],
        "sector_peers": peers["peers"],
        "sector_peer_breakouts": peers["breakout_count"],
        "sector_positive_peers": peer_positive,
        "sector_peer_avg_perf_1m": peer_avg_perf,
        "sector_perf_1m": sector_perf_1m,
        "sector_perf_3m": sector_perf_3m,
        "sector_perf_6m": sector_perf_6m,
        "sector_momentum_score": sector_momentum_score,
        "news_tone": news_tone,
        "news_titles": news_titles,
        "nifty_weekly_rsi": nifty["weekly_rsi"],
        "nifty_structure": nifty["structure"],
        "nifty_trend_state": nifty["trend_state"],
        "market_mood": nifty["mood"],
        "retail_psych": retail_psych,
        "sentiment_score": sentiment_score,
        "sentiment_components": sentiment_component_scores,
        "delivery_10d_avg_pct": delivery["avg_pct"],
        "delivery_trend": delivery["trend"],
        "corporate_action_note": DATA_NOT_PROVIDED,
        "missing_data": missing_data,
    }
