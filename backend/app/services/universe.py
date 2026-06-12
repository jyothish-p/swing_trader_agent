"""
Universe builder for NSE stock screening.

Modes:
  - `tradingview_all`: fetch the full NSE stock list from TradingView's India
    scanner and screen all available NSE stocks.
  - anything else: use the older focused large-cap/F&O oriented universe.
"""
import logging
from datetime import datetime

import httpx
import yfinance as yf
from sqlalchemy.orm import Session

from app.config import UNIVERSE_MIN_MARKET_CAP_CR, UNIVERSE_MODE
from app.models import Stock

logger = logging.getLogger(__name__)

# Hardcoded fallbacks
FALLBACK_FO_SYMBOLS = [
    "ABB", "ADANIENT", "ADANIGREEN", "ADANIPORTS", "ADANIPOWER", "ALKEM",
    "AMBUJACEM", "ANGELONE", "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ATUL",
    "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJAJHLDNG",
    "BAJFINANCE", "BALKRISIND", "BANDHANBNK", "BANKBARODA", "BEL", "BERGEPAINT",
    "BHARATFORG", "BHARTIARTL", "BHEL", "BIOCON", "BOSCHLTD", "BPCL", "BRITANNIA",
    "BSE", "CANBK", "CDSL", "CIPLA", "COALINDIA", "COFORGE", "COLPAL", "CONCOR",
    "CRISIL", "CROMPTON", "CUMMINSIND", "DABUR", "DEEPAKNTR", "DIVISLAB",
    "DIXON", "DLF", "DMART", "DRREDDY", "EICHERMOT", "EMAMILTD", "EXIDEIND",
    "FEDERALBNK", "FORTIS", "GAIL", "GLENMARK", "GODREJCP", "GODREJPROP",
    "GRANULES", "GRASIM", "HAL", "HAPPSTMNDS", "HAVELLS", "HCLTECH", "HDFCAMC",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HFCL", "HINDALCO", "HINDPETRO",
    "HINDUNILVR", "HUDCO", "ICICIBANK", "ICICIPRULI", "IDEA", "IDBI",
    "IDFCFIRSTB", "IGL", "INDIANB", "INDIGO", "INDUSINDBK", "INDUSTOWER",
    "INFY", "IPCALAB", "IRCTC", "IRFC", "ITC", "JINDALSTEL", "JSWSTEEL",
    "KAYNES", "KOTAKBANK", "LAURUSLABS", "LICI", "LT", "LTTS", "LUPIN",
    "M&M", "MANAPPURAM", "MARICO", "MARUTI", "MAXHEALTH", "MCX", "METROPOLIS",
    "MGL", "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN", "NATIONALUM",
    "NAVINFLUOR", "NESTLEIND", "NHPC", "NMDC", "NTPC", "NYKAA", "OBEROIRLTY",
    "ONGC", "PAGEIND", "PAYTM", "PERSISTENT", "PETRONET", "PFC", "PHOENIXLTD",
    "PIDILITIND", "PIIND", "PNB", "POLICYBZR", "RAMCOCEM", "RECLTD",
    "RELIANCE", "RVNL", "SAIL", "SBICARD", "SBILIFE", "SBIN", "SHREECEM",
    "SHRIRAMFIN", "SIEMENS", "SRF", "SUNPHARMA", "SUZLON", "TATACONSUM",
    "TATAELXSI", "TATACHEM", "TATAPOWER", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TORNTPHARM", "TRENT", "UBL", "ULTRACEMCO", "UPL", "VEDL",
    "VOLTAS", "WIPRO", "ZYDUSLIFE", "ACC", "IOC", "JSWENERGY",
]

FALLBACK_BROAD_UNIVERSE = list(dict.fromkeys(FALLBACK_FO_SYMBOLS + [
    "3MINDIA", "AARTIIND", "ABCAPITAL", "ABFRL", "ABSLAMC",
    "AIAENG", "AJANTPHARM", "ASTRAL", "BAJAJELEC",
    "BATAINDIA", "BDL", "BSOFT", "CANFINHOME", "CARBORUNIV", "CASTROLIND",
    "CENTURYPLY", "CESC", "CHAMBLFERT", "CHOLAFIN", "CLEAN",
    "COROMANDEL", "CUB", "CYIENT", "DALBHARAT", "DCMSHRIRAM", "DEVYANI",
    "DELHIVERY", "ELGIEQUIP", "ENDURANCE", "EQUITASBNK", "ESCORTS",
    "FINEORG", "FSL", "GICRE", "GILLETTE", "GLAXO", "GNFC",
    "GRINDWELL", "GSFC", "GUJGASLTD", "HINDCOPPER", "HONAUT",
    "IDFC", "INDHOTEL", "IOB", "IRB", "ISEC",
    "JKCEMENT", "JKLAKSHMI", "JSL", "JUBLFOOD",
    "KALYANKJIL", "KEI", "KIMS", "KEC", "KFINTECH", "KPITTECH",
    "LATENTVIEW", "LICHSGFIN", "MAHLOG",
    "MASTEK", "MFSL", "NAM-INDIA", "NAUKRI", "NIACL",
    "NLCINDIA", "OFSS", "OIL", "OLECTRA", "PGHH", "POLYCAB",
    "POONAWALLA", "POWERINDIA", "PRESTIGE", "PVRINOX", "RADICO",
    "RAJESHEXPO", "RBLBANK", "REDINGTON", "RELAXO", "SANOFI",
    "SCHAEFFLER", "SJVN", "SKFINDIA", "SONACOMS", "STARHEALTH",
    "SUMICHEM", "SUNDARMFIN", "SUNDRMFAST", "SUNTV", "SUPREMEIND",
    "SYNGENE", "TATAINVEST", "TATATECH", "THERMAX", "TIMKEN", "TIINDIA",
    "TRIDENT", "TVSMOTOR", "UNIONBANK", "UNOMINDA",
    "VGUARD", "VINATIORGA", "WHIRLPOOL",
    "ZEEL", "ZENSARTECH", "ZOMATO",
    "AFFLE", "ALOKINDS", "AMBER", "APLAPOLLO", "ASAHIINDIA", "ASTERDM",
    "ATUL", "AVANTIFEED", "AWL", "BAYERCROP", "BHAGERIA", "BIRLACORPN",
    "BORORENEW", "CAMPUS", "CAPLIPOINT", "CARYSIL", "CCL", "CEATLTD",
    "CENTURYTEX", "CGCL", "CHALET", "CHOICEIN", "COCHINSHIP",
    "CONCORDBIO", "DATAPATTNS", "DCBBANK", "DEEPAKFERT", "DELTACORP",
    "DOMS", "EASEMYTRIP", "ECLERX", "EDELWEISS", "ELECON",
    "ELECTCAST", "EMCURE", "EPL", "ETERNAL", "FINCABLES", "FLUOROCHEM",
    "GLS", "GMDCLTD", "GPIL", "GRAPHITE", "GREAVESCOT", "GSPL",
    "HAPPYFORGE", "HGS", "HFCL", "HINDWARE", "HOMEFIRST",
    "IBULHSGFIN", "ICICIGI", "INTELLECT", "IOLCP",
    "JAIBALAJI", "JAMNAAUTO", "JBCHEPHARM", "JIOFIN", "JKL",
    "JSWINFRA", "JTEKTINDIA", "KALPATPOWR", "KANSAINER", "KARURVYSYA",
    "KIRLOSENG", "KIRLOSIND", "KRBL", "KSB",
    "LALPATHLAB", "LEMONTREE", "LLOYDSME", "LTIM",
    "LUXIND", "MAZDOCK", "MEDPLUS", "METROPOLIS",
    "MHRIL", "MMTC", "MOIL", "MOTILALOFS", "MTAR",
    "NATCOPHARM", "NESCO", "NETWEB", "NEWGEN",
    "NUCLEUS", "NURECA", "NUVAMA",
    "PATANJALI", "PCBL", "POWERMECH", "PRINCEPIPE", "PRIVISCL",
    "PPLPHARMA", "QUESS",
    "RATNAMANI", "RAYMOND", "ROUTE",
    "RVNL", "SAPPHIRE", "SOLARINDS", "SONATSOFTW",
    "SPLPETRO", "SWANENERGY", "TANLA", "TATVA",
    "TEAMLEASE", "TEGA", "TITAGARH", "TTML",
    "UTI", "VARROC", "VIPIND", "VOLTAMP",
    "WELCORP", "WELSPUNLIV", "YESBANK",
]))


def _market_cap_passes_filter(market_cap_cr: float | None) -> bool:
    minimum = max(0.0, float(UNIVERSE_MIN_MARKET_CAP_CR or 0))
    if minimum <= 0:
        return True
    return float(market_cap_cr or 0) >= minimum


def _cache_threshold_for_mode() -> int:
    if UNIVERSE_MODE == "tradingview_all":
        return 1000
    return 50


async def _fetch_nse_index_constituents(index_name: str) -> list[str]:
    """Fetch constituents of an NSE index via the API."""
    url = f"https://www.nseindia.com/api/equity-stockIndices?index={index_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            await client.get("https://www.nseindia.com", headers=headers)
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                symbols = []
                for item in data.get("data", []):
                    sym = item.get("symbol", "").strip()
                    if sym and sym != "NIFTY" and not sym.startswith("NIFTY"):
                        symbols.append(sym)
                if symbols:
                    logger.info("Fetched %s stocks from index %s", len(symbols), index_name)
                    return symbols
    except Exception as exc:
        logger.warning("Index %s fetch failed: %s", index_name, exc)
    return []


async def fetch_fo_symbols() -> set[str]:
    """Fetch the F&O eligible stock list from NSE."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/csv,*/*",
    }
    url = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                raw_text = resp.text.strip()
                if raw_text and raw_text[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ\"',\t ":
                    symbols = set()
                    for line in raw_text.splitlines()[1:]:
                        parts = line.split(",")
                        if len(parts) < 2:
                            continue
                        sym = parts[1].strip().upper()
                        if (
                            sym
                            and sym != "SYMBOL"
                            and not sym.startswith("NIFTY")
                            and sym.replace("&", "").replace("-", "").isalnum()
                            and len(sym) <= 20
                        ):
                            symbols.add(sym)
                    if len(symbols) > 50:
                        logger.info("Fetched %s F&O stocks from NSE", len(symbols))
                        return symbols
                logger.warning("NSE F&O CSV looked invalid, using fallback")
    except Exception as exc:
        logger.warning("NSE F&O fetch failed: %s", exc)

    return set(FALLBACK_FO_SYMBOLS)


async def fetch_tradingview_nse_symbols() -> list[dict]:
    """Fetch the full NSE stock universe from TradingView's India scanner."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
    }
    url = "https://scanner.tradingview.com/india/scan"
    page_size = 500
    offset = 0
    rows: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            while True:
                payload = {
                    "filter": [
                        {"left": "exchange", "operation": "equal", "right": "NSE"},
                        {"left": "type", "operation": "equal", "right": "stock"},
                    ],
                    "options": {"lang": "en"},
                    "markets": ["india"],
                    "symbols": {"query": {"types": []}, "tickers": []},
                    "columns": ["name", "description", "market_cap_basic"],
                    "range": [offset, offset + page_size - 1],
                }
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.warning("TradingView universe fetch failed with HTTP %s", resp.status_code)
                    break

                payload_data = resp.json()
                batch = payload_data.get("data") or []
                if not batch:
                    break

                for item in batch:
                    symbol = item.get("s", "").split(":", 1)[-1].strip().upper()
                    if not symbol:
                        continue
                    values = item.get("d") or []
                    market_cap_raw = values[2] if len(values) > 2 else None
                    market_cap_cr = round(float(market_cap_raw) / 1e7, 2) if market_cap_raw else 0
                    rows.append({
                        "symbol": symbol,
                        "name": values[1] if len(values) > 1 else "",
                        "market_cap_cr": market_cap_cr,
                    })

                total_count = int(payload_data.get("totalCount") or 0)
                offset += len(batch)
                if offset >= total_count:
                    break
    except Exception as exc:
        logger.warning("TradingView NSE universe fetch failed: %s", exc)

    if not rows:
        return rows

    deduped = {row["symbol"]: row for row in rows}
    logger.info("Fetched %s NSE symbols from TradingView (%s unique)", len(rows), len(deduped))
    return sorted(deduped.values(), key=lambda item: item["symbol"])


def fetch_market_caps_fast(symbols: list[str], db: Session) -> dict[str, float]:
    """
    Fetch market caps, but skip symbols that already have a valid market cap in DB.
    """
    existing = {}
    for stock in db.query(Stock).filter(Stock.symbol.in_(symbols), Stock.market_cap_cr > 0).all():
        existing[stock.symbol] = stock.market_cap_cr

    need_fetch = [s for s in symbols if s not in existing]
    if not need_fetch:
        logger.info("All %s market caps cached in DB", len(symbols))
        return existing

    logger.info("Fetching market caps for %s new symbols (%s cached)...", len(need_fetch), len(existing))

    yf_symbols = [f"{s}.NS" for s in need_fetch]
    batch_size = 50
    total = len(yf_symbols)
    fetched = {}

    for i in range(0, total, batch_size):
        batch = yf_symbols[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        if batch_num % 5 == 1 or batch_num == total_batches:
            logger.info("  Market cap batch %s/%s...", batch_num, total_batches)
        try:
            tickers = yf.Tickers(" ".join(batch))
            for yf_sym in batch:
                sym = yf_sym.replace(".NS", "")
                try:
                    info = tickers.tickers[yf_sym].fast_info
                    mcap = getattr(info, "market_cap", 0) or 0
                    fetched[sym] = round(mcap / 1e7, 2)
                except Exception:
                    fetched[sym] = 0
        except Exception as exc:
            logger.warning("Market cap batch %s failed: %s", batch_num, exc)
            for yf_sym in batch:
                fetched[yf_sym.replace(".NS", "")] = 0

    return {**existing, **fetched}


async def _build_tradingview_universe(db: Session) -> list[dict]:
    """Build the broad TradingView-backed NSE universe."""
    fo_symbols = await fetch_fo_symbols()
    tradingview_symbols = await fetch_tradingview_nse_symbols()
    if not tradingview_symbols:
        return []

    symbol_map = {item["symbol"]: item for item in tradingview_symbols}
    symbols = sorted(symbol_map)
    existing_stocks = {
        stock.symbol: stock
        for stock in db.query(Stock).filter(Stock.symbol.in_(symbols)).all()
    }

    for sym in symbols:
        item = symbol_map[sym]
        stock = existing_stocks.get(sym)
        market_cap = item.get("market_cap_cr", 0)
        if stock:
            stock.name = item.get("name") or stock.name
            if market_cap > 0:
                stock.market_cap_cr = market_cap
            stock.is_fno = sym in fo_symbols
            stock.is_active = True
            stock.last_updated = datetime.utcnow()
        else:
            db.add(Stock(
                symbol=sym,
                name=item.get("name", ""),
                market_cap_cr=market_cap,
                is_fno=sym in fo_symbols,
                is_active=True,
            ))

    db.query(Stock).filter(~Stock.symbol.in_(symbols)).update(
        {Stock.is_active: False},
        synchronize_session=False,
    )
    db.commit()

    filtered = [
        {
            "symbol": item["symbol"],
            "market_cap_cr": item.get("market_cap_cr", 0),
            "is_fno": item["symbol"] in fo_symbols,
        }
        for item in tradingview_symbols
        if _market_cap_passes_filter(item.get("market_cap_cr"))
    ]
    logger.info(
        "TradingView universe ready: %s total, %s eligible, %s F&O",
        len(tradingview_symbols),
        len(filtered),
        sum(1 for item in filtered if item["is_fno"]),
    )
    return sorted(filtered, key=lambda item: item["symbol"])


async def _build_focused_universe(db: Session) -> list[dict]:
    """Build the older focused NSE universe."""
    logger.info("Building focused stock universe...")
    fo_symbols = await fetch_fo_symbols()
    logger.info("Source 1 - F&O: %s stocks", len(fo_symbols))

    nifty500 = await _fetch_nse_index_constituents("NIFTY%20500")

    all_symbols = set(fo_symbols)
    if nifty500:
        all_symbols.update(nifty500)
        logger.info("Source 2 - Nifty 500 API: %s stocks", len(nifty500))
    else:
        all_symbols.update(FALLBACK_BROAD_UNIVERSE)
        logger.info("Source 2 - Fallback broad list: %s stocks", len(FALLBACK_BROAD_UNIVERSE))

    all_symbols_list = sorted(all_symbols)
    logger.info("Total unique symbols: %s", len(all_symbols_list))

    market_caps = fetch_market_caps_fast(all_symbols_list, db)

    logger.info("Updating stock database...")
    for sym in all_symbols_list:
        mcap = market_caps.get(sym, 0)
        is_fno = sym in fo_symbols
        existing = db.query(Stock).filter(Stock.symbol == sym).first()
        if existing:
            if mcap > 0:
                existing.market_cap_cr = mcap
            existing.is_fno = is_fno
            existing.is_active = True
            existing.last_updated = datetime.utcnow()
        else:
            db.add(Stock(
                symbol=sym,
                market_cap_cr=mcap,
                is_fno=is_fno,
                is_active=True,
            ))

    db.query(Stock).filter(~Stock.symbol.in_(all_symbols_list)).update(
        {Stock.is_active: False},
        synchronize_session=False,
    )
    db.commit()

    filtered = [
        {"symbol": sym, "market_cap_cr": market_caps.get(sym, 0), "is_fno": sym in fo_symbols}
        for sym in all_symbols_list
        if _market_cap_passes_filter(market_caps.get(sym, 0))
    ]
    logger.info(
        "Focused universe ready: %s total, %s eligible, %s F&O",
        len(all_symbols_list),
        len(filtered),
        sum(1 for item in filtered if item["is_fno"]),
    )
    return filtered


async def get_filtered_universe(db: Session, force_refresh: bool = False) -> list[dict]:
    """Return the configured stock universe for screening."""
    if not force_refresh:
        existing = [s for s in db.query(Stock).filter(Stock.is_active == True).all()
                    if _market_cap_passes_filter(s.market_cap_cr)]
        if len(existing) >= _cache_threshold_for_mode():
            logger.info(
                "Using cached universe: %s stocks (mode=%s, min_mcap=%s)",
                len(existing),
                UNIVERSE_MODE,
                UNIVERSE_MIN_MARKET_CAP_CR,
            )
            return [
                {"symbol": s.symbol, "market_cap_cr": s.market_cap_cr, "is_fno": s.is_fno}
                for s in existing
            ]
        logger.info("Cached universe is too small for mode=%s, rebuilding...", UNIVERSE_MODE)

    if UNIVERSE_MODE == "tradingview_all":
        universe = await _build_tradingview_universe(db)
        if universe:
            return universe
        logger.warning("TradingView universe failed, falling back to focused universe")

    return await _build_focused_universe(db)
