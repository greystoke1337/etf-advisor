"""
Data fetcher for ETF price and metadata using yfinance.
Handles rate limiting, retries, and incremental updates.
"""
import time
import logging
from datetime import datetime, date, timedelta
from typing import Optional

import yfinance as yf
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import ETFPrice, ETFInfo, DCATransaction, FetchLog, SessionLocal

logger = logging.getLogger(__name__)

# Default ETFs to track — add more tickers here anytime
DEFAULT_ETFS = [
    "DHHF.AX",   # BetaShares Diversified All Growth
]

# How far back to fetch on first run
INITIAL_HISTORY_YEARS = 5


def fetch_etf_data(ticker: str, db: Session, full_refresh: bool = False) -> dict:
    """
    Fetch price data for a single ETF and store in database.
    Only fetches new data since the last stored date (incremental).
    Returns a summary dict.
    """
    result = {"ticker": ticker, "status": "success", "rows_added": 0, "message": ""}

    try:
        # Find the latest date we already have
        latest = db.query(func.max(ETFPrice.date)).filter(
            ETFPrice.ticker == ticker
        ).scalar()

        if latest and not full_refresh:
            start_date = latest + timedelta(days=1)
            if start_date > date.today():
                result["message"] = "Already up to date"
                return result
            period = None
            start_str = start_date.strftime("%Y-%m-%d")
        else:
            period = f"{INITIAL_HISTORY_YEARS}y"
            start_str = None

        # Fetch from Yahoo Finance with rate-limit safety
        time.sleep(1.5)  # Respect rate limits
        etf = yf.Ticker(ticker)

        if period:
            hist = etf.history(period=period, auto_adjust=True)
        else:
            hist = etf.history(start=start_str, auto_adjust=True)

        if hist.empty:
            result["message"] = "No new data available"
            return result

        # Store price data
        rows_added = 0
        for idx, row in hist.iterrows():
            price_date = idx.date() if hasattr(idx, 'date') else idx

            # Check if already exists (safety)
            exists = db.query(ETFPrice).filter(
                ETFPrice.ticker == ticker,
                ETFPrice.date == price_date
            ).first()

            if not exists:
                price = ETFPrice(
                    ticker=ticker,
                    date=price_date,
                    open=round(float(row.get("Open", 0)), 4),
                    high=round(float(row.get("High", 0)), 4),
                    low=round(float(row.get("Low", 0)), 4),
                    close=round(float(row.get("Close", 0)), 4),
                    volume=int(row.get("Volume", 0)) if pd.notna(row.get("Volume", 0)) else 0,
                    dividends=round(float(row.get("Dividends", 0)), 6),
                )
                db.add(price)
                rows_added += 1

        db.commit()
        result["rows_added"] = rows_added
        result["message"] = f"Added {rows_added} new price records"

        # Update ETF metadata
        _update_etf_info(ticker, etf, db)

        logger.info(f"[{ticker}] {result['message']}")

    except Exception as e:
        db.rollback()
        result["status"] = "error"
        result["message"] = str(e)
        logger.error(f"[{ticker}] Fetch error: {e}")

    # Log the fetch
    log = FetchLog(
        ticker=ticker,
        status=result["status"],
        rows_added=result["rows_added"],
        message=result["message"][:500]
    )
    db.add(log)
    db.commit()

    return result


def _update_etf_info(ticker: str, etf: yf.Ticker, db: Session):
    """Update or create ETF metadata record."""
    try:
        info = etf.info or {}
        funds = None
        try:
            funds = etf.funds_data
        except Exception:
            pass

        expense_ratio = None
        if funds:
            try:
                ops = funds.fund_operations
                if ops is not None and not ops.empty:
                    # Look for annual report expense ratio
                    if "Annual Report Expense Ratio (net)" in ops.index:
                        val = ops.loc["Annual Report Expense Ratio (net)"].values
                    elif "Annual Report Expense Ratio" in ops.index:
                        val = ops.loc["Annual Report Expense Ratio"].values
                    else:
                        val = []
                    if len(val) > 0 and pd.notna(val[0]):
                        expense_ratio = float(val[0])
            except Exception:
                pass

        if expense_ratio is None:
            expense_ratio = info.get("annualReportExpenseRatio")

        existing = db.query(ETFInfo).filter(ETFInfo.ticker == ticker).first()

        if existing:
            existing.name = info.get("longName") or info.get("shortName") or existing.name
            if expense_ratio is not None:
                existing.expense_ratio = expense_ratio
            existing.category = info.get("category") or existing.category
            existing.description = info.get("longBusinessSummary") or existing.description
            existing.currency = info.get("currency", "AUD")
            existing.last_updated = datetime.utcnow()
        else:
            etf_info = ETFInfo(
                ticker=ticker,
                name=info.get("longName") or info.get("shortName") or ticker,
                expense_ratio=expense_ratio,
                category=info.get("category"),
                description=info.get("longBusinessSummary"),
                currency=info.get("currency", "AUD"),
            )
            db.add(etf_info)

        db.commit()
    except Exception as e:
        logger.warning(f"[{ticker}] Metadata update failed: {e}")


def fetch_all_etfs(full_refresh: bool = False) -> list:
    """Fetch data for all tracked ETFs."""
    db = SessionLocal()
    results = []
    try:
        # Get custom tickers from DB + defaults
        custom = db.query(ETFInfo.ticker).all()
        tickers = list(set(DEFAULT_ETFS + [t[0] for t in custom]))

        for ticker in tickers:
            result = fetch_etf_data(ticker, db, full_refresh)
            results.append(result)
            time.sleep(1)  # Rate limit between tickers

    finally:
        db.close()

    return results


def simulate_dca(ticker: str, monthly_amount: float, db: Session) -> list:
    """
    Simulate dollar-cost averaging with historical data.
    Invests on the first trading day of each month.
    """
    prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker
    ).order_by(ETFPrice.date).all()

    if not prices:
        return []

    transactions = []
    current_month = None

    for price in prices:
        month_key = (price.date.year, price.date.month)
        if month_key != current_month:
            current_month = month_key
            units = monthly_amount / price.close
            transactions.append({
                "date": price.date,
                "amount": monthly_amount,
                "price": price.close,
                "units": round(units, 4),
                "total_units": 0,  # Calculated below
                "total_invested": 0,
                "current_value": 0,
            })

    # Calculate running totals
    total_units = 0
    total_invested = 0
    for t in transactions:
        total_units += t["units"]
        total_invested += t["amount"]
        t["total_units"] = round(total_units, 4)
        t["total_invested"] = round(total_invested, 2)

    # Use latest price for current value
    if prices:
        latest_price = prices[-1].close
        for t in transactions:
            t["current_value"] = round(t["total_units"] * latest_price, 2)

    return transactions
