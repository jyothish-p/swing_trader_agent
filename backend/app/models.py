"""Database models for the swing trading app."""
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, Float, String, Boolean, Date, DateTime,
    UniqueConstraint, Index, Text, JSON
)
from app.database import Base


class Stock(Base):
    """Master stock table - the F&O universe."""
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), default="")
    isin = Column(String(20), default="")
    sector = Column(String(100), default="")
    industry = Column(String(100), default="")
    market_cap_cr = Column(Float, default=0)  # in Crores
    lot_size = Column(Integer, default=0)  # F&O lot size
    is_fno = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    last_updated = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_stocks_market_cap", "market_cap_cr"),
    )


class DailyCandle(Base):
    """Daily OHLCV data - the core price data store."""
    __tablename__ = "daily_candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    adj_close = Column(Float, default=0)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_candle_symbol_date"),
        Index("ix_candles_symbol_date", "symbol", "date"),
    )


class ScreeningResult(Base):
    """Computed screening metrics for each stock per run."""
    __tablename__ = "screening_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(50), nullable=False, index=True)  # timestamp-based run ID
    run_date = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String(50), nullable=False, index=True)

    # Price metrics
    cmp = Column(Float, default=0)
    high_52w = Column(Float, default=0)
    low_52w = Column(Float, default=0)
    pct_from_52w = Column(Float, default=0)
    high_1m = Column(Float, default=0)
    is_1m_new_high = Column(Boolean, default=False)

    # Volume metrics
    today_vol = Column(Integer, default=0)
    avg_vol_20d = Column(Integer, default=0)
    avg_vol_1m = Column(Integer, default=0)
    vol_ratio_1d = Column(Float, default=0)

    # Turnover / PV
    pv_today = Column(Float, default=0)  # Price * Volume today (₹ Cr)
    pv_avg_1m = Column(Float, default=0)
    turnover_today_cr = Column(Float, default=0)
    turnover_avg_cr = Column(Float, default=0)

    # Momentum
    momentum_1d = Column(Float, default=0)
    momentum_1w = Column(Float, default=0)
    momentum_1m = Column(Float, default=0)
    momentum_3m = Column(Float, default=0)

    # Screening report flags
    in_52w_high_report = Column(Boolean, default=False)
    in_1m_high_daily_vol = Column(Boolean, default=False)
    in_1m_high_monthly_vol = Column(Boolean, default=False)
    in_oi_surge = Column(Boolean, default=False)
    in_index_movers = Column(Boolean, default=False)
    reports_count = Column(Integer, default=0)  # How many reports it appears in

    # Composite score for ranking
    composite_score = Column(Float, default=0)

    # Market cap
    market_cap_cr = Column(Float, default=0)

    __table_args__ = (
        UniqueConstraint("run_id", "symbol", name="uq_screening_run_symbol"),
        Index("ix_screening_composite", "run_id", "composite_score"),
    )


class TechnicalAnalysis(Base):
    """Detailed technical analysis for top N stocks."""
    __tablename__ = "technical_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(50), nullable=False, index=True)
    symbol = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)  # 'daily', 'weekly', 'monthly'

    # Moving averages
    ema_20 = Column(Float)
    ema_50 = Column(Float)
    ema_100 = Column(Float)
    ema_200 = Column(Float)
    sma_20 = Column(Float)
    sma_50 = Column(Float)

    # Bollinger Bands
    bb_upper = Column(Float)
    bb_middle = Column(Float)
    bb_lower = Column(Float)
    bb_width = Column(Float)

    # RSI
    rsi = Column(Float)
    rsi_signal = Column(String(20))  # 'oversold', 'neutral', 'overbought'

    # MACD
    macd = Column(Float)
    macd_signal = Column(Float)
    macd_histogram = Column(Float)
    macd_crossover = Column(String(20))  # 'bullish', 'bearish', 'none'

    # VWAP / Volume Profile
    vwap = Column(Float)
    volume_profile = Column(JSON)  # price levels with volume concentration

    # Golden/Death cross
    golden_cross = Column(Boolean, default=False)  # 50 EMA crosses above 200 EMA
    death_cross = Column(Boolean, default=False)

    # Fibonacci levels
    fib_levels = Column(JSON)  # {0: price, 0.236: price, 0.382: price, ...}

    # Gann levels
    gann_levels = Column(JSON)

    # Support / Resistance
    support_levels = Column(JSON)
    resistance_levels = Column(JSON)

    # Overall signal
    signal = Column(String(20))  # 'strong_buy', 'buy', 'neutral', 'sell', 'strong_sell'
    signal_score = Column(Float, default=0)  # -100 to +100

    last_updated = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "symbol", "timeframe", name="uq_ta_run_symbol_tf"),
    )


class DeliveryData(Base):
    """Delivery volume data from NSE."""
    __tablename__ = "delivery_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False)
    traded_volume = Column(Integer, default=0)
    delivery_volume = Column(Integer, default=0)
    delivery_pct = Column(Float, default=0)
    close_price = Column(Float, default=0)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_delivery_symbol_date"),
    )


class ScreenerRun(Base):
    """Log of each screener run."""
    __tablename__ = "screener_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(50), unique=True, nullable=False)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    status = Column(String(20), default="running")  # running, completed, failed
    total_stocks = Column(Integer, default=0)
    filtered_stocks = Column(Integer, default=0)
    top_stocks = Column(Integer, default=0)
    error_message = Column(Text)


class PortfolioEntry(Base):
    """Track stock purchases and their evolving MATE-PRO recommendations."""
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)

    # Purchase details
    buy_date = Column(Date, nullable=False)
    buy_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    buy_reason = Column(String(500), default="")  # e.g. "MATE-PRO STRONG BUY"

    # Current state (updated each screener run)
    current_price = Column(Float, default=0)
    pnl_pct = Column(Float, default=0)  # Unrealized P&L %
    pnl_amount = Column(Float, default=0)  # Unrealized P&L ₹

    # Latest MATE-PRO scores
    mate_pro_verdict = Column(String(20), default="")  # STRONG BUY / BUY / HOLD / WAIT / AVOID
    mate_pro_score = Column(Float, default=0)  # Composite score 0-100
    mate_pro_probability = Column(Float, default=0)  # Probability %
    mate_pro_action = Column(String(20), default="")  # TRADE / WAIT / HOLD / SELL
    titan_score = Column(Float, default=0)
    swing_ai_score = Column(Float, default=0)
    king_score = Column(Float, default=0)

    # Targets & stops from MATE-PRO
    stop_loss = Column(Float, default=0)
    target_1 = Column(Float, default=0)
    target_2 = Column(Float, default=0)
    target_3 = Column(Float, default=0)

    # Status
    status = Column(String(20), default="open")  # open, closed, partial
    sell_date = Column(Date)
    sell_price = Column(Float)
    realized_pnl_pct = Column(Float)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)

    # Notes
    notes = Column(Text, default="")
