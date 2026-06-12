"""
Swing Trader - NSE Stock Screening Application
FastAPI entry point.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.config import CORS_ORIGINS
from app.database import init_db
from app.routers import screener, stocks, analysis, portfolio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app):
    """Startup and shutdown logic."""
    init_db()
    logging.getLogger(__name__).info("Database initialized, app ready")
    yield


app = FastAPI(
    title="Swing Trader - NSE Stock Screener",
    description="Automated NSE F&O stock screening for swing trading",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS + ["*"],  # Allow all in dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# Register routers
app.include_router(screener.router, prefix="/api/screener", tags=["Screener"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["Stocks"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "Swing Trader NSE"}
