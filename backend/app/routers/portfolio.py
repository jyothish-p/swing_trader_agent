"""
Portfolio tracking API endpoints.
Track purchases, update with MATE-PRO scores on each run, recommend actions.
"""
import logging
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import PortfolioEntry, DailyCandle
from app.services.mate_pro import run_mate_pro_analysis
from app.services.screener import _to_python

logger = logging.getLogger(__name__)
router = APIRouter()


class AddStockRequest(BaseModel):
    symbol: str
    buy_date: str  # YYYY-MM-DD
    buy_price: float
    quantity: int
    buy_reason: str = ""
    notes: str = ""


class SellStockRequest(BaseModel):
    sell_date: str  # YYYY-MM-DD
    sell_price: float
    notes: str = ""


def _update_portfolio_with_mate_pro(entry: PortfolioEntry, db: Session):
    """Update a single portfolio entry with latest price and MATE-PRO analysis."""
    symbol = entry.symbol

    # Get latest price
    latest = db.query(DailyCandle).filter(
        DailyCandle.symbol == symbol
    ).order_by(DailyCandle.date.desc()).first()

    if latest:
        entry.current_price = latest.close
        cost = entry.buy_price * entry.quantity
        current_val = latest.close * entry.quantity
        entry.pnl_amount = round(current_val - cost, 2)
        entry.pnl_pct = round((latest.close / entry.buy_price - 1) * 100, 2)

    # Run MATE-PRO
    mp = run_mate_pro_analysis(db, symbol, allow_llm_verdict=False)
    if mp:
        entry.mate_pro_verdict = mp["composite"]["consensus_verdict"]
        entry.mate_pro_score = mp["composite"]["composite_score"]
        entry.mate_pro_probability = mp["composite"]["composite_probability"]
        entry.titan_score = mp["composite"]["model_scores"].get("TITAN", 0)
        entry.swing_ai_score = mp["composite"]["model_scores"].get("Swing_AI", 0)
        entry.king_score = mp["composite"]["model_scores"].get("KING", 0)

        # Determine action based on P&L + MATE-PRO verdict
        verdict = entry.mate_pro_verdict
        pnl = entry.pnl_pct

        if verdict in ("AVOID", "SKIP") or pnl < -5:
            entry.mate_pro_action = "SELL"
        elif verdict == "WAIT" and pnl < -3:
            entry.mate_pro_action = "SELL"
        elif verdict in ("STRONG BUY", "BUY") and pnl < 2:
            entry.mate_pro_action = "BUY MORE"
        elif verdict in ("STRONG BUY", "BUY"):
            entry.mate_pro_action = "HOLD"
        elif verdict == "HOLD":
            entry.mate_pro_action = "HOLD"
        elif verdict == "WAIT":
            entry.mate_pro_action = "HOLD"
        else:
            entry.mate_pro_action = "HOLD"

        # Update targets/stops
        entry.stop_loss = mp["levels"]["invalidation"]
        targets = mp["trade_plans"]["scanner_plan"]["targets"]
        entry.target_1 = targets.get("T1", {}).get("price", 0)
        entry.target_2 = targets.get("T2", {}).get("price", 0)
        entry.target_3 = targets.get("T3", {}).get("price", 0)

        # Check if any target hit
        if latest and entry.target_2 > 0 and latest.close >= entry.target_2:
            entry.mate_pro_action = "BOOK PROFIT"
        elif latest and entry.stop_loss > 0 and latest.close <= entry.stop_loss:
            entry.mate_pro_action = "EXIT (SL HIT)"

    entry.last_updated = datetime.utcnow()


@router.get("/")
async def get_portfolio(
    status: str = "open",
    db: Session = Depends(get_db),
):
    """Get all portfolio entries."""
    query = db.query(PortfolioEntry)
    if status != "all":
        query = query.filter(PortfolioEntry.status == status)

    entries = query.order_by(PortfolioEntry.created_at.desc()).all()

    total_invested = sum(e.buy_price * e.quantity for e in entries if e.status == "open")
    total_current = sum(e.current_price * e.quantity for e in entries if e.status == "open" and e.current_price > 0)
    total_pnl = total_current - total_invested if total_current > 0 else 0
    total_pnl_pct = round((total_current / total_invested - 1) * 100, 2) if total_invested > 0 else 0

    return _to_python({
        "total_entries": len(entries),
        "summary": {
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_current, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": total_pnl_pct,
            "action_counts": {
                "buy_more": len([e for e in entries if e.mate_pro_action == "BUY MORE"]),
                "hold": len([e for e in entries if e.mate_pro_action == "HOLD"]),
                "sell": len([e for e in entries if e.mate_pro_action in ("SELL", "BOOK PROFIT", "EXIT (SL HIT)")]),
            },
        },
        "entries": [
            {
                "id": e.id,
                "symbol": e.symbol,
                "buy_date": e.buy_date.isoformat() if e.buy_date else None,
                "buy_price": e.buy_price,
                "quantity": e.quantity,
                "buy_reason": e.buy_reason,
                "current_price": e.current_price,
                "pnl_pct": e.pnl_pct,
                "pnl_amount": e.pnl_amount,
                "invested": round(e.buy_price * e.quantity, 2),
                "current_value": round(e.current_price * e.quantity, 2) if e.current_price else 0,
                "mate_pro": {
                    "verdict": e.mate_pro_verdict,
                    "score": e.mate_pro_score,
                    "probability": e.mate_pro_probability,
                    "action": e.mate_pro_action,
                    "model_scores": {
                        "TITAN": e.titan_score,
                        "Swing_AI": e.swing_ai_score,
                        "KING": e.king_score,
                    },
                },
                "stop_loss": e.stop_loss,
                "target_1": e.target_1,
                "target_2": e.target_2,
                "target_3": e.target_3,
                "status": e.status,
                "sell_date": e.sell_date.isoformat() if e.sell_date else None,
                "sell_price": e.sell_price,
                "realized_pnl_pct": e.realized_pnl_pct,
                "notes": e.notes,
                "last_updated": e.last_updated.isoformat() if e.last_updated else None,
            }
            for e in entries
        ],
    })


@router.post("/add")
async def add_to_portfolio(
    req: AddStockRequest,
    db: Session = Depends(get_db),
):
    """Add a stock purchase to portfolio."""
    symbol = req.symbol.upper().strip()

    entry = PortfolioEntry(
        symbol=symbol,
        buy_date=datetime.strptime(req.buy_date, "%Y-%m-%d").date(),
        buy_price=req.buy_price,
        quantity=req.quantity,
        buy_reason=req.buy_reason,
        notes=req.notes,
        status="open",
    )

    # Run MATE-PRO analysis immediately
    _update_portfolio_with_mate_pro(entry, db)

    db.add(entry)
    db.commit()

    logger.info(f"Added {symbol} to portfolio: {req.quantity} @ ₹{req.buy_price}")
    return {"status": "added", "id": entry.id, "symbol": symbol}


@router.post("/refresh")
async def refresh_portfolio(db: Session = Depends(get_db)):
    """Refresh all open portfolio entries with latest prices and MATE-PRO scores."""
    entries = db.query(PortfolioEntry).filter(
        PortfolioEntry.status == "open"
    ).all()

    updated = 0
    for entry in entries:
        try:
            _update_portfolio_with_mate_pro(entry, db)
            updated += 1
        except Exception as e:
            logger.warning(f"Failed to update {entry.symbol}: {e}")

    db.commit()
    logger.info(f"Portfolio refreshed: {updated}/{len(entries)} entries updated")
    return {"status": "refreshed", "updated": updated, "total": len(entries)}


@router.post("/{entry_id}/sell")
async def sell_stock(
    entry_id: int,
    req: SellStockRequest,
    db: Session = Depends(get_db),
):
    """Mark a portfolio entry as sold."""
    entry = db.query(PortfolioEntry).filter(PortfolioEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")

    entry.status = "closed"
    entry.sell_date = datetime.strptime(req.sell_date, "%Y-%m-%d").date()
    entry.sell_price = req.sell_price
    entry.realized_pnl_pct = round((req.sell_price / entry.buy_price - 1) * 100, 2)
    entry.notes = req.notes or entry.notes
    entry.last_updated = datetime.utcnow()

    db.commit()
    logger.info(f"Sold {entry.symbol}: ₹{entry.buy_price} → ₹{req.sell_price} ({entry.realized_pnl_pct}%)")
    return {"status": "sold", "symbol": entry.symbol, "pnl_pct": entry.realized_pnl_pct}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    """Delete a portfolio entry."""
    entry = db.query(PortfolioEntry).filter(PortfolioEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")
    db.delete(entry)
    db.commit()
    return {"status": "deleted", "id": entry_id}
