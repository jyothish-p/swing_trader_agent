"""
Swing Trader - NSE Stock Screening Application
FastAPI entry point.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import CORS_ORIGINS, SERVE_FRONTEND
from app.database import init_db
from app.routers import screener, stocks, analysis, portfolio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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
    allow_origins=CORS_ORIGINS,
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


def _resolve_frontend_file(path_fragment: str) -> Path | None:
    candidate = (FRONTEND_DIST_DIR / path_fragment).resolve()
    if candidate == FRONTEND_DIST_DIR.resolve() or FRONTEND_DIST_DIR.resolve() in candidate.parents:
        return candidate
    return None


def _register_root_static_file(filename: str) -> None:
    route_path = f"/{filename}"

    @app.get(route_path, include_in_schema=False)
    async def serve_root_file() -> FileResponse:
        file_path = FRONTEND_DIST_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"{filename} not found")
        return FileResponse(file_path)


if SERVE_FRONTEND and FRONTEND_DIST_DIR.exists():
    assets_dir = FRONTEND_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    for static_name in ("favicon.svg", "icons.svg"):
        _register_root_static_file(static_name)


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")

    index_file = FRONTEND_DIST_DIR / "index.html"
    if not SERVE_FRONTEND or not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")

    if full_path:
        candidate = _resolve_frontend_file(full_path)
        if candidate and candidate.is_file():
            return FileResponse(candidate)

    return FileResponse(index_file)
