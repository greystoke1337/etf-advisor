"""
Analysis engine for ETF data.
Computes moving averages, risk metrics, DCA projections, and dividend tracking.
"""
import math
from datetime import date, timedelta
from typing import Optional
from dataclasses import dataclass, asdict

import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import ETFPrice, ETFInfo


@dataclass
class ETFSnapshot:
    """Current state summary for an ETF."""
    ticker: str
    name: str
    currency: str
    expense_ratio: Optional[float]

    # Price info
    latest_price: float
    latest_date: date
    daily_change: float
    daily_change_pct: float

    # Moving averages
    sma_50: Optional[float]
    sma_200: Optional[float]
    price_vs_sma50: Optional[float]   # % above/below
    price_vs_sma200: Optional[float]
    trend_signal: str  # "bullish", "bearish", "neutral"

    # Performance
    return_1m: Optional[float]
    return_3m: Optional[float]
    return_6m: Optional[float]
    return_1y: Optional[float]
    return_ytd: Optional[float]

    # Risk
    volatility_annual: Optional[float]
    max_drawdown: Optional[float]
    sharpe_ratio: Optional[float]

    # Dividends
    trailing_yield: Optional[float]
    total_dividends_1y: Optional[float]
    last_dividend_date: Optional[date]
    last_dividend_amount: Optional[float]

    def to_dict(self):
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, date):
                d[k] = v.isoformat()
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


def get_etf_snapshot(ticker: str, db: Session) -> Optional[ETFSnapshot]:
    """Build a complete snapshot for an ETF."""

    # Get price data as DataFrame
    prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker
    ).order_by(ETFPrice.date).all()

    if not prices or len(prices) < 2:
        return None

    df = pd.DataFrame([{
        "date": p.date,
        "close": p.close,
        "high": p.high,
        "low": p.low,
        "volume": p.volume,
        "dividends": p.dividends,
    } for p in prices])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    # ETF metadata
    info = db.query(ETFInfo).filter(ETFInfo.ticker == ticker).first()
    name = info.name if info else ticker
    currency = info.currency if info else "AUD"
    expense_ratio = info.expense_ratio if info else None

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    latest_price = float(latest["close"])
    daily_change = latest_price - float(prev["close"])
    daily_change_pct = (daily_change / float(prev["close"])) * 100

    # Moving averages
    sma_50 = float(df["close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
    sma_200 = float(df["close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None

    price_vs_sma50 = ((latest_price - sma_50) / sma_50 * 100) if sma_50 else None
    price_vs_sma200 = ((latest_price - sma_200) / sma_200 * 100) if sma_200 else None

    # Trend signal
    trend_signal = "neutral"
    if sma_50 and sma_200:
        if sma_50 > sma_200 and latest_price > sma_50:
            trend_signal = "bullish"
        elif sma_50 < sma_200 and latest_price < sma_50:
            trend_signal = "bearish"

    # Period returns
    def period_return(days: int) -> Optional[float]:
        cutoff = df.index[-1] - pd.Timedelta(days=days)
        past = df[df.index <= cutoff]
        if past.empty:
            return None
        return float((latest_price - past.iloc[-1]["close"]) / past.iloc[-1]["close"] * 100)

    return_1m = period_return(30)
    return_3m = period_return(90)
    return_6m = period_return(180)
    return_1y = period_return(365)

    # YTD return
    year_start = df[df.index >= pd.Timestamp(date(date.today().year, 1, 1))]
    return_ytd = None
    if not year_start.empty and len(year_start) > 1:
        first_close = float(year_start.iloc[0]["close"])
        return_ytd = float((latest_price - first_close) / first_close * 100)

    # Risk metrics (annualized)
    daily_returns = df["close"].pct_change().dropna()
    volatility_annual = None
    sharpe_ratio = None
    if len(daily_returns) > 20:
        vol = float(daily_returns.std() * np.sqrt(252))
        volatility_annual = round(vol * 100, 2)

        # Sharpe using 4.35% risk-free (RBA cash rate approximate)
        annual_return = float(daily_returns.mean() * 252)
        risk_free = 0.0435
        if vol > 0:
            sharpe_ratio = round((annual_return - risk_free) / vol, 2)

    # Maximum drawdown
    cummax = df["close"].cummax()
    drawdown = (df["close"] - cummax) / cummax
    max_drawdown = round(float(drawdown.min()) * 100, 2) if not drawdown.empty else None

    # Dividend analysis
    divs = df[df["dividends"] > 0]
    one_year_ago = df.index[-1] - pd.Timedelta(days=365)
    recent_divs = divs[divs.index >= one_year_ago]

    total_dividends_1y = float(recent_divs["dividends"].sum()) if not recent_divs.empty else 0.0
    trailing_yield = round((total_dividends_1y / latest_price) * 100, 2) if latest_price > 0 else None

    last_div_date = None
    last_div_amount = None
    if not divs.empty:
        last_div_date = divs.index[-1].date()
        last_div_amount = float(divs.iloc[-1]["dividends"])

    return ETFSnapshot(
        ticker=ticker,
        name=name,
        currency=currency,
        expense_ratio=expense_ratio,
        latest_price=round(latest_price, 2),
        latest_date=df.index[-1].date(),
        daily_change=round(daily_change, 2),
        daily_change_pct=round(daily_change_pct, 2),
        sma_50=round(sma_50, 2) if sma_50 else None,
        sma_200=round(sma_200, 2) if sma_200 else None,
        price_vs_sma50=round(price_vs_sma50, 2) if price_vs_sma50 else None,
        price_vs_sma200=round(price_vs_sma200, 2) if price_vs_sma200 else None,
        trend_signal=trend_signal,
        return_1m=round(return_1m, 2) if return_1m is not None else None,
        return_3m=round(return_3m, 2) if return_3m is not None else None,
        return_6m=round(return_6m, 2) if return_6m is not None else None,
        return_1y=round(return_1y, 2) if return_1y is not None else None,
        return_ytd=round(return_ytd, 2) if return_ytd is not None else None,
        volatility_annual=volatility_annual,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        trailing_yield=trailing_yield,
        total_dividends_1y=round(total_dividends_1y, 4),
        last_dividend_date=last_div_date,
        last_dividend_amount=round(last_div_amount, 4) if last_div_amount else None,
    )


def get_chart_data(ticker: str, db: Session, months: int = 12) -> dict:
    """Get price and SMA data formatted for Chart.js."""
    cutoff = date.today() - timedelta(days=months * 30)

    prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker,
        ETFPrice.date >= cutoff,
    ).order_by(ETFPrice.date).all()

    if not prices:
        return {"labels": [], "prices": [], "sma50": [], "sma200": [], "volumes": []}

    # We need more historical data for SMA-200 calculation
    all_prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker,
    ).order_by(ETFPrice.date).all()

    df = pd.DataFrame([{"date": p.date, "close": p.close, "volume": p.volume} for p in all_prices])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    df["sma50"] = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()

    # Filter to requested period
    df = df[df.index >= pd.Timestamp(cutoff)]

    return {
        "labels": [d.strftime("%Y-%m-%d") for d in df.index],
        "prices": [round(float(v), 2) for v in df["close"]],
        "sma50": [round(float(v), 2) if pd.notna(v) else None for v in df["sma50"]],
        "sma200": [round(float(v), 2) if pd.notna(v) else None for v in df["sma200"]],
        "volumes": [int(v) if pd.notna(v) else 0 for v in df["volume"]],
    }


def get_dividend_history(ticker: str, db: Session) -> list:
    """Get dividend payment history."""
    prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker,
        ETFPrice.dividends > 0,
    ).order_by(ETFPrice.date.desc()).all()

    return [{
        "date": p.date.isoformat(),
        "amount": round(p.dividends, 4),
        "price_on_date": round(p.close, 2),
        "yield_pct": round((p.dividends / p.close) * 100, 4) if p.close else 0,
    } for p in prices]


def calculate_dca_projection(
    ticker: str,
    monthly_amount: float,
    db: Session,
    lookback_years: int = 3,
) -> dict:
    """
    Project DCA outcomes using historical data.
    Shows what would have happened investing $X/month.
    """
    cutoff = date.today() - timedelta(days=lookback_years * 365)

    prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker,
        ETFPrice.date >= cutoff,
    ).order_by(ETFPrice.date).all()

    if not prices:
        return {}

    # Simulate buying on first trading day of each month
    monthly_buys = []
    current_month = None
    total_units = 0.0
    total_invested = 0.0

    for p in prices:
        month_key = (p.date.year, p.date.month)
        if month_key != current_month:
            current_month = month_key
            units = monthly_amount / p.close
            total_units += units
            total_invested += monthly_amount

            monthly_buys.append({
                "date": p.date.isoformat(),
                "price": round(p.close, 2),
                "units_bought": round(units, 4),
                "total_units": round(total_units, 4),
                "total_invested": round(total_invested, 2),
                "current_value": round(total_units * p.close, 2),
            })

    # Final stats
    latest_price = prices[-1].close if prices else 0
    final_value = round(total_units * latest_price, 2)
    total_return = final_value - total_invested
    total_return_pct = round((total_return / total_invested) * 100, 2) if total_invested > 0 else 0
    avg_cost = round(total_invested / total_units, 2) if total_units > 0 else 0

    # Add dividend income (use units held at time of each dividend)
    div_prices = db.query(ETFPrice).filter(
        ETFPrice.ticker == ticker,
        ETFPrice.date >= cutoff,
        ETFPrice.dividends > 0,
    ).order_by(ETFPrice.date).all()
    total_dividends = 0.0
    if div_prices:
        for dp in div_prices:
            # Find how many units were held on this dividend date
            units_at_date = 0.0
            for buy in monthly_buys:
                if buy["date"] <= dp.date.isoformat():
                    units_at_date = buy["total_units"]
            total_dividends += dp.dividends * units_at_date

    return {
        "monthly_amount": monthly_amount,
        "months": len(monthly_buys),
        "total_invested": round(total_invested, 2),
        "total_units": round(total_units, 4),
        "average_cost": avg_cost,
        "current_price": round(latest_price, 2),
        "current_value": final_value,
        "capital_return": round(total_return, 2),
        "capital_return_pct": total_return_pct,
        "estimated_dividends": round(total_dividends, 2),
        "total_return": round(total_return + total_dividends, 2),
        "history": monthly_buys,
    }
