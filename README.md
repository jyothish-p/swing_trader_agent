# Swing Trader - NSE Stock Screener

A full-stack stock screening and analysis app for Indian (NSE) swing trading. Screens 350+ stocks, runs 3 proprietary MATE-PRO scoring models (TITAN, Swing AI, KING), and surfaces actionable BUY/SHORT SELL signals with reasoning.

---

## Prerequisites

You need two things installed:

1. **Python 3.11+** — [Download from python.org](https://www.python.org/downloads/)
   - During install, **check "Add Python to PATH"**
2. **Node.js 18+** — [Download from nodejs.org](https://nodejs.org/)
   - The LTS version is fine

To verify both are installed, open Command Prompt and run:
```
python --version
node --version
```

---

## Quick Start (Windows)

### Option A: One-click startup
Double-click **`start.bat`** in the project root. It will:
1. Install Python dependencies
2. Install Node.js dependencies
3. Start the backend (port 8000)
4. Start the frontend (port 5174)
5. Open your browser to `http://localhost:5174`

### Option B: Manual startup

**Terminal 1 — Backend:**
```cmd
cd swing-trader\backend
pip install -r requirements.txt
python run.py
```

**Terminal 2 — Frontend:**
```cmd
cd swing-trader\frontend
npm install
npm run dev
```

Then open **http://localhost:5174** in your browser.

---

## How to Use

1. **Dashboard** — Click "Run Full Scan" to screen all NSE stocks (first run takes 3-5 minutes to download data; subsequent runs are faster thanks to caching)
2. **Actionable Now** — Shows stocks with clear BUY or SHORT SELL signals, with reasoning for each
3. **Top 20** — The highest-scoring stocks across all 3 models
4. **Stock Lookup** — Search any NSE symbol for instant MATE-PRO analysis
5. **Click any stock** — Full detail view with all technical indicators, trade plans, and model breakdowns

---

## Project Structure

```
swing-trader/
├── backend/                  # FastAPI + Python
│   ├── app/
│   │   ├── config.py         # Settings (market cap filter, ports, etc.)
│   │   ├── main.py           # FastAPI app entry point
│   │   ├── models.py         # SQLAlchemy database models
│   │   ├── database.py       # DB connection
│   │   ├── routers/          # API endpoints
│   │   │   ├── screener.py   # /api/screener/* — run scans, get results
│   │   │   ├── analysis.py   # /api/analysis/* — MATE-PRO, lookup, charts
│   │   │   ├── stocks.py     # /api/stocks/* — universe, price history
│   │   │   └── portfolio.py  # /api/portfolio/* — track trades
│   │   └── services/         # Business logic
│   │       ├── universe.py   # NSE stock universe (F&O + broad list)
│   │       ├── data_fetcher.py # yfinance historical data download
│   │       ├── screener.py   # Multi-factor screening engine
│   │       ├── mate_pro.py   # 3-model MATE-PRO scoring system
│   │       └── technical.py  # Technical analysis (EMA, RSI, Fib, etc.)
│   ├── requirements.txt
│   └── run.py
├── frontend/                 # React + Vite + Tailwind
│   ├── src/
│   │   ├── pages/            # Dashboard, Lookup, StockDetail, Portfolio
│   │   ├── components/       # StockTable, ActionableTable, etc.
│   │   └── lib/api.js        # API client
│   ├── package.json
│   └── vite.config.js
├── start.bat                 # One-click Windows startup
├── run_screener.bat          # Headless screener (for Task Scheduler)
└── README.md
```

---

## Configuration

Edit `backend/app/config.py` to adjust:
- `MIN_MARKET_CAP_CR` — Market cap filter (default: ₹5,000 Cr)
- `TOP_N_STOCKS` — Number of top stocks shown (default: 20)
- `ACTIONABLE_TOP_N` — Max actionable signals (default: 20)
- `LOOKBACK_DAYS` — Historical data window (default: 365 days)

---

## Data Storage

The SQLite database is stored at:
- `C:\Users\<YourName>\.swing_trader\data\swing_trader.db`
- (Falls back to `backend/data/` if path has no spaces)

First run downloads ~1 year of daily data for 350+ stocks. After that, only incremental updates are fetched.

---

## Scheduled Daily Scan

To automatically run the screener every morning at 9:30 AM IST:

1. Double-click **`setup_scheduler.bat`** (run once, as Administrator)
2. This creates a Windows Task Scheduler entry that triggers `run_screener.bat` daily

To verify: open Task Scheduler → look for "SwingTraderDailyScan"

---

## Troubleshooting

- **"python is not recognized"** → Reinstall Python and check "Add to PATH"
- **"npm is not recognized"** → Reinstall Node.js
- **Screener timeout** → First run downloads a lot of data. Let it finish. Subsequent runs are much faster.
- **Port already in use** → Change ports in `backend/app/config.py` (PORT) and `frontend/vite.config.js` (server.port + proxy target)
- **No stocks showing after scan** → Check the backend terminal for errors. The most common issue is yfinance rate limiting — wait a minute and try again.

---

## Optional LLM one-line verdicts

You can optionally let Gemini or OpenAI rewrite the one-line verdict for single-stock analysis.

### Gemini setup

```cmd
set LLM_VERDICTS_PROVIDER=gemini
set GEMINI_API_KEY=your_api_key_here
set GEMINI_VERDICTS_ENABLED=1
set GEMINI_VERDICTS_MODEL=gemini-2.5-flash
```

### OpenAI setup

```cmd
set LLM_VERDICTS_PROVIDER=openai
set OPENAI_API_KEY=your_api_key_here
set OPENAI_VERDICTS_ENABLED=1
set OPENAI_VERDICTS_MODEL=gpt-5.4-mini
```

### Auto-select provider

```cmd
set LLM_VERDICTS_PROVIDER=auto
```

In `auto` mode, the app prefers Gemini when both providers are enabled.

Optional tuning:

```cmd
set GEMINI_VERDICTS_MAX_OUTPUT_TOKENS=90
set OPENAI_VERDICTS_TIMEOUT_SEC=20
set OPENAI_VERDICTS_MAX_OUTPUT_TOKENS=90
```

Notes:
- If no configured provider is available, the app automatically falls back to the built-in rules-based one-line verdict.
- LLM verdicts are used for `Lookup` and stock detail analysis.
- Screener runs, batch analysis, and portfolio refresh stay rules-based to avoid extra cost and latency.

