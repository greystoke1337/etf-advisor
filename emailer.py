"""
Email report generator.
Sends weekly and monthly HTML digest emails via Resend.
"""
import os
import logging
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from database import SessionLocal, ETFInfo
from analysis import get_etf_snapshot, get_dividend_history, calculate_dca_projection

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "ETF Advisor <onboarding@resend.dev>")
APP_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")
MONTHLY_AMOUNT = float(os.environ.get("DCA_MONTHLY_AMOUNT", "200"))


def _trend_emoji(signal: str) -> str:
    return {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(signal, "⚪")


def _format_pct(val, include_sign=True) -> str:
    if val is None:
        return "—"
    sign = "+" if val > 0 and include_sign else ""
    return f"{sign}{val:.2f}%"


def _format_dollar(val) -> str:
    if val is None:
        return "—"
    return f"${val:,.2f}"


def generate_weekly_html() -> str:
    """Generate weekly email digest HTML."""
    db = SessionLocal()
    try:
        etfs = db.query(ETFInfo).all()
        if not etfs:
            return "<p>No ETFs tracked yet. Visit your dashboard to set up tracking.</p>"

        sections = []
        for etf in etfs:
            snap = get_etf_snapshot(etf.ticker, db)
            if not snap:
                continue

            divs = get_dividend_history(etf.ticker, db)
            recent_div = divs[0] if divs else None

            sections.append(f"""
            <div style="background:#f8f7f4; border-radius:12px; padding:24px; margin-bottom:20px;">
                <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:16px;">
                    <div>
                        <h2 style="margin:0; font-family:'DM Serif Display',Georgia,serif; color:#1a1a1a; font-size:22px;">
                            {snap.ticker.replace('.AX','')}
                        </h2>
                        <p style="margin:2px 0 0; color:#666; font-size:13px;">{snap.name}</p>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-size:28px; font-weight:700; color:#1a1a1a; font-family:'JetBrains Mono',monospace;">
                            {_format_dollar(snap.latest_price)}
                        </span>
                        <br>
                        <span style="color:{'#16a34a' if snap.daily_change >= 0 else '#dc2626'}; font-size:14px; font-weight:600;">
                            {'+' if snap.daily_change >= 0 else ''}{snap.daily_change:.2f} ({_format_pct(snap.daily_change_pct)})
                        </span>
                    </div>
                </div>

                <table style="width:100%; border-collapse:collapse; font-size:14px;">
                    <tr>
                        <td style="padding:8px 0; color:#666;">Trend</td>
                        <td style="padding:8px 0; text-align:right; font-weight:600;">
                            {_trend_emoji(snap.trend_signal)} {snap.trend_signal.title()}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0; color:#666;">SMA-50 / SMA-200</td>
                        <td style="padding:8px 0; text-align:right; font-family:monospace;">
                            {_format_dollar(snap.sma_50)} / {_format_dollar(snap.sma_200)}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0; color:#666;">1M / 3M / YTD</td>
                        <td style="padding:8px 0; text-align:right; font-family:monospace;">
                            {_format_pct(snap.return_1m)} / {_format_pct(snap.return_3m)} / {_format_pct(snap.return_ytd)}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0; color:#666;">Trailing Yield</td>
                        <td style="padding:8px 0; text-align:right;">{_format_pct(snap.trailing_yield, False)}</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0; color:#666;">Volatility (ann.)</td>
                        <td style="padding:8px 0; text-align:right;">{_format_pct(snap.volatility_annual, False)}</td>
                    </tr>
                    {'<tr><td style="padding:8px 0; color:#666;">Last Distribution</td><td style="padding:8px 0; text-align:right;">' + _format_dollar(recent_div["amount"]) + ' on ' + recent_div["date"] + '</td></tr>' if recent_div else ''}
                </table>
            </div>
            """)

        # DCA summary for first ETF
        dca_section = ""
        if etfs:
            dca = calculate_dca_projection(etfs[0].ticker, MONTHLY_AMOUNT, db, lookback_years=3)
            if dca:
                dca_section = f"""
                <div style="background:#eef6ee; border-radius:12px; padding:24px; margin-bottom:20px;">
                    <h3 style="margin:0 0 12px; color:#1a1a1a; font-family:'DM Serif Display',Georgia,serif;">
                        DCA Snapshot — {_format_dollar(MONTHLY_AMOUNT)}/month
                    </h3>
                    <p style="color:#444; font-size:14px; line-height:1.6; margin:0;">
                        If you'd invested {_format_dollar(MONTHLY_AMOUNT)}/month over the last {dca['months']} months,
                        you'd have invested <strong>{_format_dollar(dca['total_invested'])}</strong>
                        now worth <strong>{_format_dollar(dca['current_value'])}</strong>
                        — a return of <strong>{_format_pct(dca['capital_return_pct'])}</strong>.
                        Your average cost would be <strong>{_format_dollar(dca['average_cost'])}</strong> vs
                        today's price of <strong>{_format_dollar(dca['current_price'])}</strong>.
                    </p>
                </div>
                """

        today = date.today()
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
        </head>
        <body style="margin:0; padding:0; background:#ffffff; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
            <div style="max-width:600px; margin:0 auto; padding:32px 20px;">
                <div style="margin-bottom:28px;">
                    <h1 style="margin:0; font-family:'DM Serif Display',Georgia,serif; font-size:28px; color:#1a1a1a;">
                        Weekly ETF Digest
                    </h1>
                    <p style="margin:4px 0 0; color:#888; font-size:14px;">
                        Week of {today.strftime('%B %d, %Y')}
                    </p>
                </div>

                {''.join(sections)}
                {dca_section}

                <div style="text-align:center; padding:20px 0; border-top:1px solid #eee;">
                    <a href="https://{APP_URL}" style="color:#2563eb; text-decoration:none; font-size:14px;">
                        Open Dashboard →
                    </a>
                    <p style="color:#aaa; font-size:12px; margin:12px 0 0;">
                        This is factual market data for personal use only, not financial advice.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    finally:
        db.close()


async def send_email(subject: str, html: str):
    """Send an email via Resend API."""
    if not RESEND_API_KEY or not EMAIL_TO:
        logger.warning("Email not configured — set RESEND_API_KEY and EMAIL_TO env vars")
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": EMAIL_FROM,
                    "to": [EMAIL_TO],
                    "subject": subject,
                    "html": html,
                },
            )
            if response.status_code == 200:
                logger.info(f"Email sent: {subject}")
                return True
            else:
                logger.error(f"Email failed ({response.status_code}): {response.text}")
                return False
    except Exception as e:
        logger.error(f"Email error: {e}")
        return False


async def send_weekly_digest():
    """Generate and send the weekly digest email."""
    html = generate_weekly_html()
    today = date.today()
    subject = f"📊 ETF Weekly Digest — {today.strftime('%b %d, %Y')}"
    return await send_email(subject, html)
