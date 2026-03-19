"""
Database setup and models for ETF Advisor.
Uses PostgreSQL on Railway via SQLAlchemy.
"""
import os
from datetime import datetime, date
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, DateTime,
    Boolean, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///etf_advisor.db")
# Railway Postgres URLs use "postgres://" but SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ETFPrice(Base):
    """Daily OHLCV price data for tracked ETFs."""
    __tablename__ = "etf_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    dividends = Column(Float, default=0.0)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_ticker_date"),
        Index("ix_ticker_date", "ticker", "date"),
    )


class ETFInfo(Base):
    """Metadata about each tracked ETF."""
    __tablename__ = "etf_info"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, unique=True)
    name = Column(String(200))
    expense_ratio = Column(Float)
    category = Column(String(100))
    description = Column(Text)
    currency = Column(String(10), default="AUD")
    last_updated = Column(DateTime, default=datetime.utcnow)


class DCATransaction(Base):
    """Track dollar-cost averaging purchases."""
    __tablename__ = "dca_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False)
    amount_invested = Column(Float, nullable=False)  # AUD
    price_per_unit = Column(Float, nullable=False)
    units_bought = Column(Float, nullable=False)
    is_simulated = Column(Boolean, default=True)


class FetchLog(Base):
    """Log of data fetch operations for monitoring."""
    __tablename__ = "fetch_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    ticker = Column(String(20))
    status = Column(String(20))  # success, error
    rows_added = Column(Integer, default=0)
    message = Column(Text)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
