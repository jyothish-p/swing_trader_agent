#!/usr/bin/env python3
"""
Headless Screener — runs the full scan pipeline without the web UI.
Designed for Windows Task Scheduler / cron.

Usage:  python run_scheduled_scan.py

What it does:
  1. Initializes the database
  2. Refreshes the stock universe (market caps, F&O list)
  3. Downloads latest price data for all stocks
  4. Runs the multi-factor screener
  5. Runs MATE-PRO analysis on all screened stocks
  6. Extracts actionable BUY/SHORT SELL signals
  7. Prints a summary to console (and logs to file)

The results are saved in the SQLite database and will appear
in the web dashboard next time you open it.
"""
import sys
import os
import logging
import asyncio
from datetime import datetime

# Ensure we can import the app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import DATA_DIR
from app.database import SessionLocal, init_db

# Set up logging to both console and file
log_file = DATA_DIR / "scheduled_scan.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(log_file), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduled_scan")


async def main():
    start = datetime.now()
    logger.info("=" * 60)
    logger.info(f"SCHEDULED SCAN starting at {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # Step 1: Init DB
    init_db()
    db = SessionLocal()

    try:
        # Step 2: Refresh universe
        logger.info("Step 1/4: Refreshing stock universe...")
        from app.services.universe import get_filtered_universe
        universe = await get_filtered_universe(db, force_refresh=True)
        logger.info(f"  Universe: {len(universe)} stocks pass market cap filter")

        # Step 3: Run screener (downloads data + screens)
        logger.info("Step 2/4: Running screener...")
        from app.services.screener import run_screener
        screener_result = run_screener(db, universe, force_refresh=False)
        top_stocks = screener_result.get("top_stocks", [])
        all_metrics = screener_result.get("all_metrics", [])
        logger.info(f"  Screener: {len(all_metrics)} stocks analyzed, top {len(top_stocks)} selected")

        # Step 4: Run MATE-PRO
        logger.info("Step 3/4: Running MATE-PRO analysis...")
        from app.services.mate_pro import run_mate_pro_batch, extract_actionable
        symbols_for_mp = [s["symbol"] for s in all_metrics] if all_metrics else [s["symbol"] for s in top_stocks]
        mate_pro_results = run_mate_pro_batch(db, symbols_for_mp[:100])  # Cap at 100 for speed
        logger.info(f"  MATE-PRO: {len(mate_pro_results)} stocks scored")

        # Step 5: Extract actionable
        logger.info("Step 4/4: Extracting actionable signals...")
        actionable = extract_actionable(mate_pro_results)
        buys = [a for a in actionable if a["action_type"] == "BUY"]
        shorts = [a for a in actionable if a["action_type"] == "SHORT SELL"]

        # Print summary
        elapsed = (datetime.now() - start).total_seconds()
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"SCAN COMPLETE in {elapsed:.0f} seconds")
        logger.info(f"  Stocks screened: {len(all_metrics)}")
        logger.info(f"  MATE-PRO scored: {len(mate_pro_results)}")
        logger.info(f"  BUY signals:     {len(buys)}")
        logger.info(f"  SHORT signals:   {len(shorts)}")
        logger.info("")

        if buys:
            logger.info("── BUY Candidates ──")
            for b in buys[:10]:
                logger.info(f"  {b['symbol']:15s} Score:{b['composite_score']:5.1f}  {b['verdict']:12s}  {b.get('reason','')}")

        if shorts:
            logger.info("── SHORT SELL Candidates ──")
            for s in shorts[:10]:
                logger.info(f"  {s['symbol']:15s} Score:{s['composite_score']:5.1f}  {s['verdict']:12s}  {s.get('reason','')}")

        logger.info("=" * 60)
        logger.info(f"Log saved to: {log_file}")

    except Exception as e:
        logger.error(f"Scan failed: {e}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
