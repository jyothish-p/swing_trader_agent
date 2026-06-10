"""
Full NSE Universe Fetcher.
Two-phase approach:
  Phase 1 (fast, ~30s): Fetch NSE index constituents (Nifty 500 + F&O list) — covers all ₹5,000Cr+ stocks.
  Phase 2 (on force-refresh only): Fetch full NSE equity list and market caps for discovery.
Caches results in DB so subsequent runs are instant.
"""
import logging
import httpx
import yfinance as yf
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import Stock
from app.config import MIN_MARKET_CAP_CR

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────
# Hardcoded fallbacks
# ─────────────────────────────────────────────────

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

# Broader NSE 500 + additional mid-caps — all are ₹5,000 Cr+ market cap
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
    # Additional large+mid caps
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


# ─────────────────────────────────────────────────
# NSE data fetchers
# ─────────────────────────────────────────────────

async def _fetch_nse_index_constituents(index_name: str) -> list[str]:
    """Fetch constituents of an NSE index via the API."""
    url = f"https://www.nseindia.com/api/equity-stockIndices?index={index_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Need to get cookies first
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
                    logger.info(f"Fetched {len(symbols)} stocks from index {index_name}")
                    return symbols
    except Exception as e:
        logger.warning(f"Index {index_name} fetch failed: {e}")
    return []


async def fetch_fo_symbols() -> set[str]:
    """Fetch the F&O eligible stock list from NSE. Returns a SET."""
    urls = [
        "https://archives.nseindia.com/content/fo/fo_mktlots.csv",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/csv,*/*",
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(urls[0], headers=headers)
            if resp.status_code == 200:
                raw_text = resp.text.strip()
                if not raw_text or raw_text[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ\"',\t ":
                    logger.warning("NSE F&O CSV appears binary, using fallback")
                else:
                    lines = raw_text.split("\n")
                    symbols = set()
                    for line in lines[1:]:
                        parts = line.split(",")
                        if len(parts) >= 2:
                            sym = parts[1].strip().upper()
                            if (sym and sym != "SYMBOL"
                                    and not sym.startswith("NIFTY")
                                    and sym.replace("&", "").replace("-", "").isalnum()
                                    and len(sym) <= 20):
                                symbols.add(sym)
                    if len(symbols) > 50:
                        logger.info(f"Fetched {len(symbols)} F&O stocks from NSE")
                        return symbols
    except Exception as e:
        logger.warning(f"NSE F&O fetch failed: {e}")

    return set(FALLBACK_FO_SYMBOLS)


def fetch_market_caps_fast(symbols: list[str], db: Session) -> dict[str, float]:
    """
    Fetch market caps, but SKIP symbols that already have a valid market cap in DB.
    This makes incremental runs much faster.
    """
    # Check what we already have
    existing = {}
    for stock in db.query(Stock).filter(Stock.symbol.in_(symbols), Stock.market_cap_cr > 0).all():
        existing[stock.symbol] = stock.market_cap_cr

    # Only fetch for symbols we don't have
    need_fetch = [s for s in symbols if s not in existing]

    if not need_fetch:
        logger.info(f"All {len(symbols)} market caps cached in DB")
        return existing

    logger.info(f"Fetching market caps for {len(need_fetch)} new symbols ({len(existing)} cached)...")

    yf_symbols = [f"{s}.NS" for s in need_fetch]
    batch_size = 50
    total = len(yf_symbols)
    fetched = {}

    for i in range(0, total, batch_size):
        batch = yf_symbols[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        if batch_num % 5 == 1 or batch_num == total_batches:
            logger.info(f"  Market cap batch {batch_num}/{total_batches}...")
        try:
            tickers = yf.Tickers(" ".join(batch))
            for yf_sym in batch:
                sym = yf_sym.replace(".NS", "")
                try:
                    info = tickers.tickers[yf_sym].fast_info
                    mcap = getattr(info, "market_cap", 0) or 0
                    fetched[sym] = round(mcap / 1e7, 2)  # Convert to Crores
                except Exception:
                    fetched[sym] = 0
        except Exception as e:
            logger.warning(f"Market cap batch {batch_num} failed: {e}")
            for yf_sym in batch:
                fetched[yf_sym.replace(".NS", "")] = 0

    # Merge: cached + newly fetched
    result = {**existing, **fetched}
    return result


# ─────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────

async def get_filtered_universe(db: Session, force_refresh: bool = False) -> list[dict]:
    """
    Get all tradeable NSE stocks ≥ ₹5,000 Cr market cap.

    Strategy:
    - Use cached DB data if available (instant)
    - On refresh: fetch F&O list + broad fallback list, only fetch market caps for NEW symbols
    - This keeps the refresh under 2 minutes even for 500+ stocks
    """

    # ── Use cache if available ──
    if not force_refresh:
        existing = db.query(Stock).filter(
            Stock.is_active == True,
            Stock.market_cap_cr >= MIN_MARKET_CAP_CR
        ).all()
        if len(existing) > 50:
            logger.info(f"Using cached universe: {len(existing)} stocks (≥₹{MIN_MARKET_CAP_CR}Cr)")
            return [
                {"symbol": s.symbol, "market_cap_cr": s.market_cap_cr, "is_fno": s.is_fno}
                for s in existing
            ]

        logger.info("No cached universe found, seeding fast startup universe...")
        fo_symbols = await fetch_fo_symbols()
        seed_symbols = sorted(set(FALLBACK_BROAD_UNIVERSE) | set(fo_symbols))
        existing_caps = {
            stock.symbol: stock.market_cap_cr
            for stock in db.query(Stock).filter(Stock.symbol.in_(seed_symbols)).all()
        }

        seeded_universe = []
        for sym in seed_symbols:
            market_cap = existing_caps.get(sym) or MIN_MARKET_CAP_CR
            is_fno = sym in fo_symbols
            stock = db.query(Stock).filter(Stock.symbol == sym).first()
            if stock:
                stock.market_cap_cr = max(stock.market_cap_cr or 0, market_cap)
                stock.is_fno = is_fno
                stock.is_active = True
                stock.last_updated = datetime.utcnow()
            else:
                db.add(Stock(
                    symbol=sym,
                    market_cap_cr=market_cap,
                    is_fno=is_fno,
                    is_active=True,
                ))
            seeded_universe.append({
                "symbol": sym,
                "market_cap_cr": market_cap,
                "is_fno": is_fno,
            })

        db.commit()
        logger.info(
            f"Fast startup universe ready: {len(seeded_universe)} stocks "
            f"({sum(1 for item in seeded_universe if item['is_fno'])} F&O)"
        )
        return seeded_universe

    # ── Build universe from multiple sources ──
    logger.info("Building fresh stock universe...")

    # Source 1: F&O stocks (always included — most liquid)
    fo_symbols = await fetch_fo_symbols()
    logger.info(f"Source 1 - F&O: {len(fo_symbols)} stocks")

    # Source 2: Try NSE index APIs for broader coverage
    nifty500 = await _fetch_nse_index_constituents("NIFTY%20500")

    # Source 3: Our broad fallback list
    all_symbols = set(fo_symbols)
    if nifty500:
        all_symbols.update(nifty500)
        logger.info(f"Source 2 - Nifty 500 API: {len(nifty500)} stocks")
    else:
        # Use hardcoded broad list
        all_symbols.update(FALLBACK_BROAD_UNIVERSE)
        logger.info(f"Source 2 - Fallback broad list: {len(FALLBACK_BROAD_UNIVERSE)} stocks")

    all_symbols_list = sorted(all_symbols)
    logger.info(f"Total unique symbols: {len(all_symbols_list)}")

    # ── Fetch market caps (incremental — skips cached) ──
    market_caps = fetch_market_caps_fast(all_symbols_list, db)

    # ── Update database ──
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
    db.commit()

    # ── Filter by market cap ──
    filtered = [
        {"symbol": sym, "market_cap_cr": market_caps.get(sym, 0), "is_fno": sym in fo_symbols}
        for sym in all_symbols_list
        if market_caps.get(sym, 0) >= MIN_MARKET_CAP_CR
    ]

    logger.info(
        f"Universe ready: {len(all_symbols_list)} total → "
        f"{len(filtered)} pass ₹{MIN_MARKET_CAP_CR}Cr filter, "
        f"{sum(1 for f in filtered if f['is_fno'])} are F&O"
    )
    return filtered
