"""
Screener API endpoints.
Run screener, get results, export to Excel.
"""
import logging
import asyncio
import json
from threading import Thread
from pathlib import Path
import numpy as np
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db, SessionLocal
from app.models import ScreeningResult, ScreenerRun
from app.config import DATA_DIR
from app.services.universe import get_filtered_universe
from app.services.data_fetcher import bulk_download_historical
from app.services.screener import run_screener, _to_python
from app.services.technical import run_full_analysis
from app.services.mate_pro import run_mate_pro_batch, extract_actionable
from app.services.live_data import get_live_quote

logger = logging.getLogger(__name__)
router = APIRouter()
RUN_JOBS: dict[str, dict] = {}
RUNS_DIR = Path(DATA_DIR) / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
STALE_RUN_TIMEOUT = timedelta(minutes=10)


def _run_snapshot_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def _save_run_snapshot(run_id: str, result: dict) -> None:
    try:
        _run_snapshot_path(run_id).write_text(
            json.dumps(_to_python(result), ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to save run snapshot for %s: %s", run_id, exc)


def _load_run_snapshot(run_id: str) -> dict | None:
    path = _run_snapshot_path(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load run snapshot for %s: %s", run_id, exc)
        return None


def _normalize_results_payload(payload: dict) -> dict:
    """Return a consistent `/results/{run_id}` response shape."""
    if "stocks" in payload and "total" in payload:
        return payload

    stocks = payload.get("all_stocks") or payload.get("top_stocks") or []
    return {
        "run_id": payload.get("run_id"),
        "total": len(stocks),
        "stocks": stocks,
        "actionable_stocks": payload.get("actionable_stocks") or [],
        "mate_pro_summary": payload.get("mate_pro_summary"),
    }


def _mp_summary(mp: dict) -> dict:
    titan = (mp.get("models") or {}).get("titan") or {}
    titan_v19 = (mp.get("models") or {}).get("titan_v19") or {}
    return {
        "composite_score": mp["composite"]["composite_score"],
        "composite_probability": mp["composite"]["composite_probability"],
        "consensus_verdict": mp["composite"]["consensus_verdict"],
        "one_line_verdict": mp.get("one_line_verdict"),
        "one_line_verdict_source": mp.get("one_line_verdict_source"),
        "agreement": mp["composite"]["agreement"],
        "model_scores": mp["composite"]["model_scores"],
        "model_verdicts": mp["composite"]["model_verdicts"],
        "action": mp["trade_plans"]["scanner_plan"]["action"],
        "trigger": mp["levels"]["trigger"],
        "stop_loss": mp["levels"]["invalidation"],
        "sl_pct": mp["metrics"]["sl_pct"],
        "targets": mp["trade_plans"]["scanner_plan"]["targets"],
        "rr_t2": mp["trade_plans"]["scanner_plan"]["rr_t2"],
        "pattern": mp["context"]["pattern"],
        "phase": mp["context"]["phase"],
        "titan_v20": {
            "model": titan.get("model"),
            "liquidity_gate": titan.get("liquidity_gate"),
            "selection_grade": titan.get("selection_grade"),
            "selection_action": titan.get("selection_action"),
            "setup_family": titan.get("setup_family"),
            "base_weekly_score": titan.get("base_weekly_score"),
            "sector_momentum_score": ((titan.get("sector_context") or {}).get("sector_momentum_score")),
            "sector_index": ((titan.get("sector_context") or {}).get("sector_index")),
            "sector_weekly_rsi": ((titan.get("sector_context") or {}).get("sector_weekly_rsi")),
            "sector_structure": ((titan.get("sector_context") or {}).get("sector_structure")),
            "sector_perf_1m": ((titan.get("sector_context") or {}).get("sector_perf_1m")),
            "sector_perf_3m": ((titan.get("sector_context") or {}).get("sector_perf_3m")),
            "sector_positive_peers": ((titan.get("sector_context") or {}).get("sector_positive_peers")),
            "sector_peer_avg_perf_1m": ((titan.get("sector_context") or {}).get("sector_peer_avg_perf_1m")),
            "news_tone": ((titan.get("sentiment_filter") or {}).get("news_tone")),
            "market_mood": ((titan.get("sentiment_filter") or {}).get("nifty_mood")),
            "retail_psych": ((titan.get("sentiment_filter") or {}).get("retail_psych")),
            "sentiment_score": ((titan.get("sentiment_filter") or {}).get("sentiment_score")),
        },
        "titan_v19": {
            "model": titan_v19.get("model"),
            "liquidity_gate": titan_v19.get("liquidity_gate"),
            "selection_grade": titan_v19.get("selection_grade"),
            "selection_action": titan_v19.get("selection_action"),
            "setup_family": titan_v19.get("setup_family"),
        },
    }


def _status_payload(
    run_id: str,
    status: str,
    message: str,
    error: str | None = None,
) -> dict:
    """Return a lightweight status payload for polling."""
    return {
        "status": status,
        "message": message,
        "run_id": run_id,
        "result": None,
        "error": error,
    }


def _mark_run_completed(
    db: Session,
    run_id: str | None,
    filtered_stocks: int,
    top_stocks: int,
) -> None:
    if not run_id:
        return

    run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
    if not run_record:
        return

    run_record.status = "completed"
    run_record.completed_at = datetime.utcnow()
    run_record.filtered_stocks = filtered_stocks
    run_record.top_stocks = top_stocks
    db.commit()


async def _execute_screener_pipeline(
    db: Session,
    force_refresh: bool = False,
    run_id: str | None = None,
):
    """Run the full screener pipeline and return the dashboard payload."""
    # Step 1: Get universe
    logger.info("Step 1: Fetching F&O universe...")
    universe = await get_filtered_universe(db, force_refresh=force_refresh)
    symbols = [s["symbol"] for s in universe]
    logger.info(f"Universe: {len(symbols)} stocks")

    # Step 2: Download data
    logger.info("Step 2: Downloading historical data...")
    fetch_result = bulk_download_historical(
        symbols, db, full_refresh=force_refresh
    )
    logger.info(
        f"Data: {len(fetch_result['success'])} fetched, "
        f"{len(fetch_result['skipped'])} cached, "
        f"{len(fetch_result['failed'])} failed"
    )

    # Step 3-4: Run screener
    logger.info("Step 3-4: Running screener...")
    screener_result = run_screener(db, symbols, run_id=run_id)

    # Step 5: Technical analysis for top stocks
    logger.info("Step 5: Running technical analysis on top stocks...")
    top_symbols = [s["symbol"] for s in screener_result["top_stocks"]]
    ta_results = {}
    for sym in top_symbols:
        try:
            ta = run_full_analysis(db, sym, screener_result["run_id"])
            ta_results[sym] = {
                tf: {
                    "signal": data.get("signal", "neutral"),
                    "signal_score": data.get("signal_score", 0),
                    "rsi": data.get("rsi"),
                    "macd_crossover": data.get("macd_crossover"),
                    "golden_cross": data.get("golden_cross"),
                }
                for tf, data in ta.items()
                if isinstance(data, dict) and "signal" in data
            }
        except Exception as e:
            logger.warning(f"TA failed for {sym}: {e}")
            ta_results[sym] = {}

    for stock in screener_result["top_stocks"]:
        sym = stock["symbol"]
        if sym in ta_results:
            stock["technical"] = ta_results[sym]

    all_analyzed_symbols = [m["symbol"] for m in screener_result.get("all_metrics", [])]
    mate_pro_symbols = all_analyzed_symbols if all_analyzed_symbols else top_symbols
    logger.info(f"Step 6: Running MATE-PRO on {len(mate_pro_symbols)} stocks...")
    mate_pro_results = run_mate_pro_batch(db, mate_pro_symbols, mode="batch", allow_llm_verdict=False)
    mate_pro_map = {r["symbol"]: r for r in mate_pro_results}

    for stock in screener_result["top_stocks"]:
        sym = stock["symbol"]
        if sym in mate_pro_map:
            stock["mate_pro"] = _mp_summary(mate_pro_map[sym])

    all_stocks = []
    for i, stock in enumerate(screener_result.get("all_metrics", []), start=1):
        row = _to_python({
            "rank": i,
            "symbol": stock["symbol"],
            "cmp": stock.get("cmp"),
            "high_52w": stock.get("high_52w"),
            "pct_from_52w": stock.get("pct_from_52w"),
            "is_1m_new_high": stock.get("is_1m_new_high"),
            "vol_ratio_1d": stock.get("vol_ratio_1d"),
            "turnover_avg_cr": stock.get("turnover_avg_cr"),
            "momentum_1w": stock.get("momentum_1w"),
            "momentum_1m": stock.get("momentum_1m"),
            "reports_count": stock.get("reports_count", 0),
            "composite_score": stock.get("composite_score"),
            "market_cap_cr": stock.get("market_cap_cr"),
            "reports": {
                "52w_high": stock.get("in_52w_high_report", False),
                "1m_high_daily_vol": stock.get("in_1m_high_daily_vol", False),
                "1m_high_monthly_vol": stock.get("in_1m_high_monthly_vol", False),
                "oi_surge": stock.get("in_oi_surge", False),
                "index_movers": stock.get("in_index_movers", False),
            },
        })
        mp = mate_pro_map.get(stock["symbol"])
        if mp:
            row["mate_pro"] = _mp_summary(mp)
        all_stocks.append(row)

    logger.info("Step 7: Extracting actionable stocks...")
    actionable_stocks = extract_actionable(mate_pro_results, top_n=0)

    try:
        from concurrent.futures import ThreadPoolExecutor

        def _fetch(sym):
            try:
                return sym, get_live_quote(sym)
            except Exception:
                return sym, None

        syms = [s["symbol"] for s in screener_result.get("top_stocks", [])]
        with ThreadPoolExecutor(max_workers=6) as ex:
            for sym, live in ex.map(_fetch, syms):
                for st in screener_result.get("top_stocks", []):
                    if st["symbol"] == sym:
                        if live and live.get("last_price") is not None:
                            st["cmp"] = live.get("last_price")
                        st["live"] = live
                        break
    except Exception as e:
        logger.warning("Live quote enrichment during run failed: %s", e)

    _mark_run_completed(
        db,
        screener_result["run_id"],
        filtered_stocks=len(all_stocks),
        top_stocks=len(screener_result["top_stocks"]),
    )

    return _to_python({
        "status": "success",
        "run_id": screener_result["run_id"],
        "universe_size": len(symbols),
        "data_fetch": {
            "fetched": len(fetch_result["success"]),
            "cached": len(fetch_result["skipped"]),
            "failed": len(fetch_result["failed"]),
            "elapsed_seconds": fetch_result["elapsed_seconds"],
        },
        "screening": {
            "analyzed": screener_result["total_analyzed"],
            "elapsed_seconds": screener_result["elapsed_seconds"],
        },
        "all_stocks": all_stocks,
        "top_stocks": screener_result["top_stocks"],
        "actionable_stocks": actionable_stocks,
        "mate_pro_summary": {
            "total_analyzed": len(mate_pro_results),
            "strong_buy": len([r for r in mate_pro_results if r["composite"]["consensus_verdict"] == "STRONG BUY"]),
            "buy": len([r for r in mate_pro_results if r["composite"]["consensus_verdict"] == "BUY"]),
            "hold": len([r for r in mate_pro_results if r["composite"]["consensus_verdict"] == "HOLD"]),
            "wait": len([r for r in mate_pro_results if r["composite"]["consensus_verdict"] == "WAIT"]),
            "avoid": len([r for r in mate_pro_results if r["composite"]["consensus_verdict"] == "AVOID"]),
        },
    })


def _run_screener_job(run_id: str, force_refresh: bool):
    db = SessionLocal()
    try:
        RUN_JOBS[run_id] = {
            "status": "running",
            "message": "Fetching universe and price history...",
            "result": None,
            "error": None,
        }
        result = asyncio.run(_execute_screener_pipeline(db, force_refresh=force_refresh, run_id=run_id))
        _save_run_snapshot(run_id, result)
        RUN_JOBS[run_id] = {
            "status": "completed",
            "message": "Completed",
            "result": result,
            "error": None,
        }
    except Exception as e:
        logger.error("Background screener run failed: %s", e, exc_info=True)
        db.rollback()
        run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
        if run_record:
            run_record.status = "failed"
            run_record.completed_at = datetime.utcnow()
            db.commit()
        RUN_JOBS[run_id] = {
            "status": "failed",
            "message": "Failed",
            "result": None,
            "error": str(e),
        }
    finally:
        db.close()


@router.post("/run")
async def run_full_screener(
    force_refresh: bool = Query(False, description="Force re-download all data"),
    db: Session = Depends(get_db),
):
    """
    Run the complete screening pipeline:
    1. Fetch F&O universe
    2. Download/update historical data
    3. Run all 5 screening reports
    4. Cross-reference and rank top 20
    5. Run technical analysis on top 20
    """
    try:
        return await _execute_screener_pipeline(db, force_refresh=force_refresh)
    except Exception as e:
        logger.error(f"Screener run failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run/async")
async def start_screener_run(
    force_refresh: bool = Query(False, description="Force re-download all data"),
):
    """Start a screener run in the background and return immediately."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    db = SessionLocal()
    try:
        run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
        if not run_record:
            db.add(ScreenerRun(
                run_id=run_id,
                started_at=datetime.utcnow(),
                status="running",
                total_stocks=0,
            ))
            db.commit()
    finally:
        db.close()
    RUN_JOBS[run_id] = {
        "status": "running",
        "message": "Starting screener...",
        "result": None,
        "error": None,
    }
    Thread(target=_run_screener_job, args=(run_id, force_refresh), daemon=True).start()
    return {"status": "running", "run_id": run_id}


@router.get("/status/{run_id}")
def get_run_status(run_id: str, db: Session = Depends(get_db)):
    """Get background run status and final result when available."""
    job = RUN_JOBS.get(run_id)
    if job:
        return _to_python(job)

    run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
    if not run_record:
        return _status_payload(run_id, "unknown", "Run not found")

    if run_record.status == "completed":
        return _status_payload(run_id, "completed", "Completed")

    if run_record.status == "failed":
        RUN_JOBS[run_id] = {
            "status": "failed",
            "message": "Failed",
            "result": None,
            "error": run_record.error_message or "Run failed",
        }
        return _status_payload(run_id, "failed", "Failed", run_record.error_message or "Run failed")

    if run_record.started_at and datetime.utcnow() - run_record.started_at > STALE_RUN_TIMEOUT:
        run_record.status = "failed"
        run_record.completed_at = datetime.utcnow()
        run_record.error_message = "Run stalled. Please start a new screener run."
        db.commit()
        RUN_JOBS[run_id] = {
            "status": "failed",
            "message": "Failed",
            "result": None,
            "error": run_record.error_message,
        }
        return _status_payload(run_id, "failed", "Failed", run_record.error_message)

    return _status_payload(run_id, "running", "Running screener...")


@router.get("/results/{run_id}")
def get_results(run_id: str, db: Session = Depends(get_db)):
    """Get screening results for a specific run, enriched with MATE-PRO scores."""
    cached_job = RUN_JOBS.get(run_id)
    if cached_job and cached_job.get("status") == "running":
        raise HTTPException(status_code=409, detail="Run still in progress. Please retry in a few seconds.")
    if cached_job and cached_job.get("status") == "completed" and cached_job.get("result"):
        normalized = _normalize_results_payload(cached_job["result"])
        return _to_python(normalized)

    snapshot = _load_run_snapshot(run_id)
    if snapshot:
        return _to_python(_normalize_results_payload(snapshot))

    run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
    if run_record and run_record.status == "running":
        raise HTTPException(status_code=409, detail="Run still in progress. Please retry in a few seconds.")

    results = db.query(ScreeningResult).filter(
        ScreeningResult.run_id == run_id
    ).order_by(ScreeningResult.composite_score.desc()).all()

    if not results:
        raise HTTPException(status_code=404, detail="Run not found")

    logger.info("Building saved run payload from screening results only: %s (%s stocks)", run_id, len(results))

    stocks = []
    for i, r in enumerate(results):
        stock = {
            "rank": i + 1,
            "symbol": r.symbol,
            "cmp": r.cmp,
            "high_52w": r.high_52w,
            "pct_from_52w": r.pct_from_52w,
            "is_1m_new_high": r.is_1m_new_high,
            "vol_ratio_1d": r.vol_ratio_1d,
            "turnover_avg_cr": r.turnover_avg_cr,
            "momentum_1w": r.momentum_1w,
            "momentum_1m": r.momentum_1m,
            "reports_count": r.reports_count,
            "composite_score": r.composite_score,
            "market_cap_cr": r.market_cap_cr,
            "reports": {
                "52w_high": r.in_52w_high_report,
                "1m_high_daily_vol": r.in_1m_high_daily_vol,
                "1m_high_monthly_vol": r.in_1m_high_monthly_vol,
                "oi_surge": r.in_oi_surge,
                "index_movers": r.in_index_movers,
            },
        }
        stocks.append(stock)

    payload = _to_python({
        "run_id": run_id,
        "total": len(results),
        "stocks": stocks,
        "actionable_stocks": [],
        "mate_pro_summary": None,
    })
    _save_run_snapshot(run_id, payload)
    return _normalize_results_payload(payload)


@router.get("/runs")
def list_runs(limit: int = 10, db: Session = Depends(get_db)):
    """List recent screener runs."""
    runs = db.query(ScreenerRun).order_by(
        ScreenerRun.started_at.desc()
    ).limit(limit).all()

    return {
        "runs": [
            {
                "run_id": r.run_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "total_stocks": r.total_stocks,
                "filtered_stocks": r.filtered_stocks,
                "top_stocks": r.top_stocks,
            }
            for r in runs
        ]
    }
