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
from app.models import ScreeningResult, ScreenerRun, ScreenerRunSnapshot
from app.config import (
    DATA_DIR,
    MATE_PRO_MAX_SYMBOLS,
    MATE_PRO_RESULT_HYDRATION_LIMIT,
    SCREENER_STALE_RUN_MINUTES,
)
from app.services.universe import get_filtered_universe
from app.services.data_fetcher import bulk_download_historical
from app.services.screener import run_screener, _to_python
from app.services.technical import run_full_analysis
from app.services.mate_pro import MODEL_WEIGHTS, run_mate_pro_batch, extract_actionable
from app.services.live_data import get_live_quote

logger = logging.getLogger(__name__)
router = APIRouter()
RUN_JOBS: dict[str, dict] = {}
RUNS_DIR = Path(DATA_DIR) / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
STALE_RUN_TIMEOUT = timedelta(minutes=SCREENER_STALE_RUN_MINUTES)


def _run_snapshot_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def _save_run_snapshot(run_id: str, result: dict, db: Session | None = None) -> None:
    payload = _to_python(result)
    try:
        _run_snapshot_path(run_id).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to save run snapshot for %s: %s", run_id, exc)

    if db is None:
        return

    try:
        snapshot = db.query(ScreenerRunSnapshot).filter(ScreenerRunSnapshot.run_id == run_id).first()
        if snapshot:
            snapshot.payload = payload
            snapshot.updated_at = datetime.utcnow()
        else:
            db.add(ScreenerRunSnapshot(
                run_id=run_id,
                payload=payload,
                updated_at=datetime.utcnow(),
            ))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Failed to save DB run snapshot for %s: %s", run_id, exc)


def _load_run_snapshot(run_id: str, db: Session | None = None) -> dict | None:
    if db is not None:
        try:
            snapshot = db.query(ScreenerRunSnapshot).filter(ScreenerRunSnapshot.run_id == run_id).first()
            if snapshot:
                return _to_python(snapshot.payload)
        except Exception as exc:
            logger.warning("Failed to load DB run snapshot for %s: %s", run_id, exc)

    path = _run_snapshot_path(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load run snapshot for %s: %s", run_id, exc)
        return None


def _set_run_job(
    run_id: str,
    status: str,
    message: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    RUN_JOBS[run_id] = {
        "status": status,
        "message": message,
        "result": result,
        "error": error,
    }


def _update_run_message(run_id: str | None, message: str) -> None:
    if not run_id:
        return

    job = RUN_JOBS.get(run_id) or {
        "status": "running",
        "result": None,
        "error": None,
    }
    job["status"] = "running"
    job["message"] = message
    RUN_JOBS[run_id] = job


def _fail_stale_runs(db: Session) -> None:
    cutoff = datetime.utcnow() - STALE_RUN_TIMEOUT
    stale_runs = db.query(ScreenerRun).filter(
        ScreenerRun.status == "running",
        ScreenerRun.started_at < cutoff,
    ).all()

    for run in stale_runs:
        run.status = "failed"
        run.completed_at = datetime.utcnow()
        run.error_message = (
            f"Run exceeded {SCREENER_STALE_RUN_MINUTES} minutes. "
            "Please start a new screener run."
        )
        _set_run_job(run.run_id, "failed", "Failed", error=run.error_message)

    if stale_runs:
        db.commit()


def _get_active_run(db: Session) -> ScreenerRun | None:
    _fail_stale_runs(db)
    return db.query(ScreenerRun).filter(
        ScreenerRun.status == "running",
    ).order_by(
        ScreenerRun.started_at.desc(),
    ).first()


def mark_interrupted_runs() -> None:
    """Clear running DB rows after a process restart or deploy."""
    db = SessionLocal()
    try:
        running_runs = db.query(ScreenerRun).filter(ScreenerRun.status == "running").all()
        for run in running_runs:
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.error_message = (
                "Application restarted before this run finished. "
                "Please start a new screener run."
            )
        if running_runs:
            db.commit()
            logger.info("Marked %s interrupted screener runs as failed", len(running_runs))
    finally:
        db.close()


def _normalize_results_payload(payload: dict) -> dict:
    """Return a consistent `/results/{run_id}` response shape."""
    if "stocks" in payload and "total" in payload:
        return {
            **payload,
            "full_engine_complete": _payload_has_full_engine(payload),
        }

    stocks = payload.get("all_stocks") or payload.get("top_stocks") or []
    return {
        "run_id": payload.get("run_id"),
        "total": len(stocks),
        "stocks": stocks,
        "actionable_stocks": payload.get("actionable_stocks") or [],
        "mate_pro_summary": payload.get("mate_pro_summary"),
        "full_engine_complete": _payload_has_full_engine(payload),
    }


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    return list(dict.fromkeys(str(symbol).upper() for symbol in symbols if symbol))


def _payload_has_full_engine(payload: dict | None) -> bool:
    if not payload:
        return False

    rows = payload.get("all_stocks") or payload.get("stocks") or payload.get("top_stocks") or []
    if not rows:
        return False

    required_keys = set(MODEL_WEIGHTS)
    return all(
        required_keys.issubset(set(((row.get("mate_pro") or {}).get("model_scores") or {}).keys()))
        for row in rows
    )


def _summarize_mate_pro_rows(rows: list[dict]) -> dict | None:
    verdicts = [
        (row.get("mate_pro") or {}).get("consensus_verdict")
        for row in rows
        if row.get("mate_pro")
    ]
    if not verdicts:
        return None

    return {
        "total_analyzed": len(verdicts),
        "strong_buy": len([v for v in verdicts if v == "STRONG BUY"]),
        "buy": len([v for v in verdicts if v == "BUY"]),
        "hold": len([v for v in verdicts if v == "HOLD"]),
        "wait": len([v for v in verdicts if v == "WAIT"]),
        "avoid": len([v for v in verdicts if v == "AVOID"]),
    }


def _has_current_mate_pro(row: dict) -> bool:
    mate_pro = row.get("mate_pro") or {}
    return bool(mate_pro) and mate_pro.get("model_weights") == MODEL_WEIGHTS


def _ensure_complete_mate_pro_payload(db: Session, run_id: str, payload: dict) -> dict:
    """Attach missing MATE-PRO rows before the dashboard receives results."""
    normalized = _normalize_results_payload(payload)
    rows = normalized.get("stocks") or []
    missing_symbols = [
        str(row.get("symbol", "")).upper()
        for row in rows
        if row.get("symbol") and not _has_current_mate_pro(row)
    ]
    if MATE_PRO_RESULT_HYDRATION_LIMIT > 0:
        missing_symbols = missing_symbols[:MATE_PRO_RESULT_HYDRATION_LIMIT]

    if not missing_symbols:
        normalized["mate_pro_summary"] = normalized.get("mate_pro_summary") or _summarize_mate_pro_rows(rows)
        return normalized

    if MATE_PRO_RESULT_HYDRATION_LIMIT <= 0:
        logger.info("Skipping saved-result MATE-PRO hydration for %s; hydration limit is disabled", run_id)
        normalized["mate_pro_summary"] = normalized.get("mate_pro_summary") or _summarize_mate_pro_rows(rows)
        return normalized

    logger.info(
        "Hydrating %s missing MATE-PRO rows before returning dashboard results for %s",
        len(missing_symbols),
        run_id,
    )
    computed_results = run_mate_pro_batch(db, missing_symbols, mode="batch", allow_llm_verdict=False)
    computed_map = {result["symbol"]: result for result in computed_results}

    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        result = computed_map.get(symbol)
        if result:
            row["mate_pro"] = _mp_summary(result)
            row.pop("mate_pro_error", None)
        elif symbol in missing_symbols:
            row["mate_pro_error"] = "MATE-PRO unavailable for this symbol"

    normalized["stocks"] = rows
    normalized["total"] = len(rows)
    normalized["mate_pro_summary"] = _summarize_mate_pro_rows(rows)
    if computed_results and not normalized.get("actionable_stocks"):
        normalized["actionable_stocks"] = extract_actionable(computed_results, top_n=0)

    _save_run_snapshot(run_id, normalized, db)
    return normalized


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
        "model_weights": mp["composite"].get("model_weights"),
        "models": mp.get("models"),
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
        "backtest_report": mp.get("backtest_report"),
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
    _update_run_message(run_id, "Fetching NSE universe...")
    logger.info("Step 1: Fetching F&O universe...")
    universe = await get_filtered_universe(db, force_refresh=force_refresh)
    symbols = [s["symbol"] for s in universe]
    logger.info(f"Universe: {len(symbols)} stocks")

    # Step 2: Download data
    _update_run_message(run_id, f"Downloading price history for {len(symbols)} stocks...")
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
    _update_run_message(run_id, "Running screener reports...")
    logger.info("Step 3-4: Running screener...")
    screener_result = run_screener(db, symbols, run_id=run_id)

    # Step 5: Technical analysis for top stocks
    _update_run_message(run_id, "Running technical analysis on top stocks...")
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
    candidate_symbols = _dedupe_symbols(top_symbols + all_analyzed_symbols)
    if MATE_PRO_MAX_SYMBOLS > 0:
        mate_pro_symbols = candidate_symbols[:MATE_PRO_MAX_SYMBOLS]
    else:
        mate_pro_symbols = candidate_symbols
    _update_run_message(
        run_id,
        f"Running MATE-PRO scoring on {len(mate_pro_symbols)} priority stocks...",
    )
    logger.info(
        "Step 6: Running MATE-PRO on %s of %s candidate stocks",
        len(mate_pro_symbols),
        len(candidate_symbols),
    )
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
    _update_run_message(run_id, "Finalizing dashboard results...")
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
        "full_engine_complete": len(mate_pro_map) == len(all_stocks),
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
        _set_run_job(run_id, "running", "Fetching universe and price history...")
        result = asyncio.run(_execute_screener_pipeline(db, force_refresh=force_refresh, run_id=run_id))
        _save_run_snapshot(run_id, result, db)
        _set_run_job(run_id, "completed", "Completed", result=result)
    except Exception as e:
        logger.error("Background screener run failed: %s", e, exc_info=True)
        db.rollback()
        run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
        if run_record:
            run_record.status = "failed"
            run_record.completed_at = datetime.utcnow()
            run_record.error_message = str(e)
            db.commit()
        _set_run_job(run_id, "failed", "Failed", error=str(e))
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
    db = SessionLocal()
    try:
        active_run = _get_active_run(db)
        if active_run:
            _set_run_job(
                active_run.run_id,
                "running",
                "A screener run is already in progress...",
            )
            return {
                "status": "running",
                "run_id": active_run.run_id,
                "message": "A screener run is already in progress...",
                "reused": True,
            }

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
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
    _set_run_job(run_id, "running", "Starting screener...")
    Thread(target=_run_screener_job, args=(run_id, force_refresh), daemon=True).start()
    return {"status": "running", "run_id": run_id}


@router.get("/status/{run_id}")
def get_run_status(run_id: str, db: Session = Depends(get_db)):
    """Get background run status and final result when available."""
    job = RUN_JOBS.get(run_id)
    if job:
        if job.get("status") == "completed" and job.get("result"):
            job = {
                **job,
                "result": _ensure_complete_mate_pro_payload(db, run_id, job["result"]),
            }
        return _to_python(job)

    run_record = db.query(ScreenerRun).filter(ScreenerRun.run_id == run_id).first()
    if not run_record:
        return _status_payload(run_id, "unknown", "Run not found")

    if run_record.status == "completed":
        return _status_payload(run_id, "completed", "Completed")

    if run_record.status == "failed":
        _set_run_job(run_id, "failed", "Failed", error=run_record.error_message or "Run failed")
        return _status_payload(run_id, "failed", "Failed", run_record.error_message or "Run failed")

    if run_record.started_at and datetime.utcnow() - run_record.started_at > STALE_RUN_TIMEOUT:
        run_record.status = "failed"
        run_record.completed_at = datetime.utcnow()
        run_record.error_message = (
            f"Run exceeded {SCREENER_STALE_RUN_MINUTES} minutes. "
            "Please start a new screener run."
        )
        db.commit()
        _set_run_job(run_id, "failed", "Failed", error=run_record.error_message)
        return _status_payload(run_id, "failed", "Failed", run_record.error_message)

    return _status_payload(run_id, "running", "Running screener...")


@router.get("/results/{run_id}")
def get_results(run_id: str, db: Session = Depends(get_db)):
    """Get screening results for a specific run, enriched with MATE-PRO scores."""
    cached_job = RUN_JOBS.get(run_id)
    if cached_job and cached_job.get("status") == "running":
        raise HTTPException(status_code=409, detail="Run still in progress. Please retry in a few seconds.")
    if cached_job and cached_job.get("status") == "completed" and cached_job.get("result"):
        normalized = _ensure_complete_mate_pro_payload(db, run_id, cached_job["result"])
        return _to_python(normalized)

    snapshot = _load_run_snapshot(run_id, db)
    if snapshot:
        return _to_python(_ensure_complete_mate_pro_payload(db, run_id, snapshot))

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
        "full_engine_complete": False,
    })
    _save_run_snapshot(run_id, payload, db)
    return _to_python(_ensure_complete_mate_pro_payload(db, run_id, payload))


@router.get("/runs")
def list_runs(limit: int = 10, db: Session = Depends(get_db)):
    """List recent screener runs."""
    _fail_stale_runs(db)
    runs = db.query(ScreenerRun).order_by(
        ScreenerRun.started_at.desc()
    ).limit(limit).all()
    run_ids = [run.run_id for run in runs]
    snapshots = {
        snapshot.run_id: snapshot.payload
        for snapshot in db.query(ScreenerRunSnapshot).filter(
            ScreenerRunSnapshot.run_id.in_(run_ids),
        ).all()
    } if run_ids else {}

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
                "has_full_results": _payload_has_full_engine(snapshots.get(r.run_id)),
            }
            for r in runs
        ]
    }
