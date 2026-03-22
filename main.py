"""
ETF Advisor — Main application.
FastAPI web app with background scheduler for data fetching and email reports.
"""
import os
import asyncio
import logging
from datetime import date, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import init_db, get_db, SessionLocal, ETFInfo, ETFPrice, FetchLog
from fetcher import fetch_all_etfs, DEFAULT_ETFS, fetch_etf_data
from analysis import get_etf_snapshot, get_chart_data, get_dividend_history, calculate_dca_projection
from emailer import send_weekly_digest, generate_weekly_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MONTHLY_AMOUNT = float(os.environ.get("DCA_MONTHLY_AMOUNT", "200"))
scheduler = AsyncIOScheduler()


# --- Scheduled Jobs ---

async def scheduled_fetch():
    """Run data fetch in a thread to avoid blocking the event loop."""
    logger.info("⏰ Scheduled data fetch starting...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, fetch_all_etfs)
    for r in results:
        logger.info(f"  {r['ticker']}: {r['message']}")
    logger.info("✅ Scheduled fetch complete")


async def scheduled_weekly_email():
    """Send weekly digest email."""
    logger.info("📧 Sending weekly digest...")
    await send_weekly_digest()


# --- App Lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting ETF Advisor...")
    init_db()

    # Initial data fetch if database is empty
    db = SessionLocal()
    count = db.query(ETFPrice).count()
    db.close()
    if count == 0:
        logger.info("📥 Empty database — running initial data fetch...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fetch_all_etfs)

    # Schedule daily fetch at 7:00 AM UTC (5:00 PM AEST, after ASX close)
    scheduler.add_job(scheduled_fetch, CronTrigger(hour=7, minute=0), id="daily_fetch")

    # Schedule weekly email on Sunday at 8:00 AM UTC (6:00 PM AEST)
    scheduler.add_job(scheduled_weekly_email, CronTrigger(day_of_week="sun", hour=8, minute=0), id="weekly_email")

    scheduler.start()
    logger.info("📅 Scheduler started (daily fetch 07:00 UTC, weekly email Sun 08:00 UTC)")

    yield

    # Shutdown
    scheduler.shutdown()
    logger.info("👋 ETF Advisor stopped")


app = FastAPI(title="ETF Advisor", lifespan=lifespan)


# --- API Routes ---

@app.get("/api/snapshot/{ticker}")
def api_snapshot(ticker: str, db: Session = Depends(get_db)):
    """Get current ETF snapshot with all metrics."""
    snapshot = get_etf_snapshot(ticker, db)
    if not snapshot:
        return JSONResponse({"error": "No data for ticker"}, status_code=404)
    return snapshot.to_dict()


@app.get("/api/chart/{ticker}")
def api_chart(ticker: str, months: int = Query(12, ge=1, le=120), db: Session = Depends(get_db)):
    """Get chart data for a ticker."""
    return get_chart_data(ticker, db, months)


@app.get("/api/dividends/{ticker}")
def api_dividends(ticker: str, db: Session = Depends(get_db)):
    """Get dividend history."""
    return get_dividend_history(ticker, db)


@app.get("/api/dca/{ticker}")
def api_dca(
    ticker: str,
    amount: float = Query(MONTHLY_AMOUNT),
    years: int = Query(3, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """Get DCA projection."""
    return calculate_dca_projection(ticker, amount, db, years)


@app.get("/api/etfs")
def api_etfs(db: Session = Depends(get_db)):
    """List all tracked ETFs."""
    etfs = db.query(ETFInfo).all()
    return [{"ticker": e.ticker, "name": e.name, "expense_ratio": e.expense_ratio} for e in etfs]


@app.post("/api/fetch")
async def api_trigger_fetch():
    """Manually trigger a data fetch."""
    await scheduled_fetch()
    return {"status": "ok", "message": "Fetch complete"}


@app.post("/api/add-etf")
def api_add_etf(ticker: str = Query(...), db: Session = Depends(get_db)):
    """Add a new ETF to track."""
    # Normalize ticker
    t = ticker.upper()
    if not t.endswith(".AX") and "." not in t:
        t = t + ".AX"

    existing = db.query(ETFInfo).filter(ETFInfo.ticker == t).first()
    if existing:
        return {"status": "exists", "ticker": t}

    # Fetch initial data
    result = fetch_etf_data(t, db, full_refresh=True)
    return {"status": result["status"], "ticker": t, "message": result["message"]}


@app.get("/api/email-preview")
def api_email_preview():
    """Preview the weekly email digest."""
    return HTMLResponse(generate_weekly_html())


@app.get("/api/logs")
def api_logs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """Get recent fetch logs."""
    logs = db.query(FetchLog).order_by(FetchLog.timestamp.desc()).limit(limit).all()
    return [{
        "timestamp": l.timestamp.isoformat(),
        "ticker": l.ticker,
        "status": l.status,
        "rows_added": l.rows_added,
        "message": l.message,
    } for l in logs]


# --- Dashboard ---

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF Advisor</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💲</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
    <style>
        :root {
            --bg: #faf9f6;
            --surface: #ffffff;
            --surface-dim: #f3f1ec;
            --text: #1a1a1a;
            --text-muted: #777;
            --text-faint: #aaa;
            --accent: #2d6a4f;
            --accent-light: #d8f3dc;
            --danger: #c1292e;
            --danger-light: #fde8e8;
            --border: #e8e6e1;
            --font-display: 'DM Serif Display', Georgia, serif;
            --font-body: 'Instrument Sans', -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
            --radius: 12px;
            --shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03);
        }

        /* PFD (cockpit) theme overrides */
        [data-theme="pfd"] {
            --bg: #0a0e14;
            --surface: #111820;
            --surface-dim: #0d1218;
            --text: #d0d0d0;
            --text-muted: #5a6a7a;
            --text-faint: #3a4a5a;
            --accent: #00ff41;
            --accent-light: rgba(0,255,65,0.08);
            --danger: #ff3333;
            --danger-light: rgba(255,51,51,0.08);
            --border: #1e2a3a;
            --font-display: 'Share Tech Mono', 'JetBrains Mono', monospace;
            --font-body: 'Share Tech Mono', 'JetBrains Mono', monospace;
            --font-mono: 'Share Tech Mono', 'JetBrains Mono', monospace;
            --radius: 0px;
            --shadow: 0 0 8px rgba(0,229,255,0.05), inset 0 0 1px rgba(0,229,255,0.1);
        }

        /* PFD scanline overlay */
        [data-theme="pfd"] body {
            background-image: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.15) 2px, rgba(0,0,0,0.15) 4px);
            background-attachment: fixed;
        }

        /* PFD CRT vignette */
        [data-theme="pfd"] body::after {
            content: '';
            position: fixed;
            inset: 0;
            background: radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.4) 100%);
            pointer-events: none;
            z-index: 9999;
        }

        /* PFD glowing readouts */
        [data-theme="pfd"] .price-big { color: #00e5ff; text-shadow: 0 0 6px rgba(0,229,255,0.4); }
        [data-theme="pfd"] .metric-value { color: #00e5ff; text-shadow: 0 0 6px rgba(0,229,255,0.4); }
        [data-theme="pfd"] .dca-stat-value { color: #00ff41; text-shadow: 0 0 6px rgba(0,255,65,0.4); }
        [data-theme="pfd"] .div-table td { color: #00ff41; }
        [data-theme="pfd"] .positive { color: #00ff41; text-shadow: 0 0 6px rgba(0,255,65,0.4); }
        [data-theme="pfd"] .negative { color: #ff3333; }
        [data-theme="pfd"] .dca-section h2 { color: #ffbf00; text-shadow: 0 0 6px rgba(255,191,0,0.4); }

        /* PFD trend badges */
        [data-theme="pfd"] .trend-bullish { background: rgba(0,255,65,0.1); color: #00ff41; border: 1px solid #00ff41; }
        [data-theme="pfd"] .trend-bearish { background: rgba(255,51,51,0.1); color: #ff3333; border: 1px solid #ff3333; }
        [data-theme="pfd"] .trend-neutral { background: rgba(255,191,0,0.08); color: #ffbf00; border: 1px solid #ffbf00; }

        /* PFD layout tweaks */
        [data-theme="pfd"] .container { max-width: 1100px; }
        [data-theme="pfd"] .metrics-grid { gap: 2px; }
        [data-theme="pfd"] .dca-stats { gap: 2px; }
        [data-theme="pfd"] header h1 { color: #00e5ff; text-shadow: 0 0 6px rgba(0,229,255,0.4); letter-spacing: 0.15em; text-transform: uppercase; font-size: 18px; }

        /* PFD annunciator accent line on metric cards */
        [data-theme="pfd"] .metric-card { position: relative; }
        [data-theme="pfd"] .metric-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            background: #00e5ff;
            opacity: 0.3;
        }

        /* PFD chart crosshair */
        [data-theme="pfd"] .chart-canvas-wrap::after {
            content: '';
            position: absolute;
            top: 50%; left: 0; right: 0;
            height: 1px;
            background: rgba(0,229,255,0.15);
            pointer-events: none;
        }

        /* PFD period tabs */
        [data-theme="pfd"] .period-tabs { gap: 0; background: transparent; padding: 0; border-radius: 0; }
        [data-theme="pfd"] .period-tab { border-radius: 0; margin-left: -1px; background: var(--surface-dim); }
        [data-theme="pfd"] .period-tab:first-child { margin-left: 0; }
        [data-theme="pfd"] .period-tab.active {
            background: rgba(0,229,255,0.1);
            color: #00e5ff;
            border-color: #00e5ff;
            box-shadow: 0 0 6px rgba(0,229,255,0.4);
            z-index: 1;
            position: relative;
        }

        /* PFD input & buttons */
        [data-theme="pfd"] .add-etf-bar input { background: var(--surface-dim); color: #00e5ff; }
        [data-theme="pfd"] .add-etf-bar input:focus { border-color: #00e5ff; box-shadow: 0 0 6px rgba(0,229,255,0.4); }
        [data-theme="pfd"] .btn { color: #00e5ff; text-transform: uppercase; letter-spacing: 0.08em; }
        [data-theme="pfd"] .btn:hover { border-color: #00e5ff; box-shadow: 0 0 6px rgba(0,229,255,0.4); }
        [data-theme="pfd"] .btn-primary { background: rgba(0,229,255,0.1); color: #00e5ff; border-color: #00e5ff; }

        /* PFD dividend table */
        [data-theme="pfd"] .div-table th { border-bottom-color: #00e5ff; }
        [data-theme="pfd"] .div-table tr:hover td { background: rgba(0,229,255,0.04); }

        /* Theme toggle switch */
        .theme-switch {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            user-select: none;
        }
        .theme-switch input {
            appearance: none;
            -webkit-appearance: none;
            width: 36px;
            height: 20px;
            background: var(--surface-dim);
            border: 1.5px solid var(--border);
            border-radius: 10px;
            position: relative;
            cursor: pointer;
            transition: all 0.2s;
        }
        .theme-switch input::before {
            content: '';
            position: absolute;
            top: 2px;
            left: 2px;
            width: 14px;
            height: 14px;
            background: var(--text-muted);
            border-radius: 50%;
            transition: all 0.2s;
        }
        .theme-switch input:checked {
            background: rgba(0,229,255,0.15);
            border-color: #00e5ff;
        }
        .theme-switch input:checked::before {
            transform: translateX(16px);
            background: #00e5ff;
        }
        .theme-switch-label {
            font-family: var(--font-mono);
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: var(--font-body);
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }

        .container {
            max-width: 960px;
            margin: 0 auto;
            padding: 40px 24px;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            margin-bottom: 40px;
            padding-bottom: 24px;
            border-bottom: 2px solid var(--text);
        }

        header h1 {
            font-family: var(--font-display);
            font-size: 32px;
            font-weight: 400;
            letter-spacing: -0.02em;
        }

        header .subtitle {
            color: var(--text-muted);
            font-size: 13px;
            margin-top: 4px;
        }

        header .actions {
            display: flex; gap: 8px; align-items: center;
        }

        .btn {
            font-family: var(--font-body);
            font-size: 13px;
            font-weight: 600;
            padding: 8px 16px;
            border-radius: 8px;
            border: 1.5px solid var(--border);
            background: var(--surface);
            color: var(--text);
            cursor: pointer;
            transition: all 0.15s;
        }
        .btn:hover { border-color: var(--text); }
        .btn-primary {
            background: var(--text);
            color: var(--bg);
            border-color: var(--text);
        }
        .btn-primary:hover { opacity: 0.85; }

        /* Price Hero */
        .price-hero {
            background: var(--surface);
            border-radius: var(--radius);
            padding: 32px;
            box-shadow: var(--shadow);
            margin-bottom: 24px;
        }

        .price-hero .ticker-row {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 20px;
        }

        .ticker-name {
            font-family: var(--font-display);
            font-size: 24px;
        }

        .ticker-meta {
            color: var(--text-muted);
            font-size: 13px;
            margin-top: 2px;
        }

        .price-display {
            text-align: right;
        }

        .price-big {
            font-family: var(--font-mono);
            font-size: 36px;
            font-weight: 600;
            letter-spacing: -0.02em;
        }

        .price-change {
            font-family: var(--font-mono);
            font-size: 15px;
            font-weight: 500;
            margin-top: 2px;
        }

        .positive { color: var(--accent); }
        .negative { color: var(--danger); }

        .trend-badge {
            display: inline-block;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 4px 10px;
            border-radius: 6px;
            margin-top: 8px;
        }
        .trend-bullish { background: var(--accent-light); color: var(--accent); }
        .trend-bearish { background: var(--danger-light); color: var(--danger); }
        .trend-neutral { background: var(--surface-dim); color: var(--text-muted); }

        /* Metric Grid */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .metric-card {
            background: var(--surface);
            border-radius: var(--radius);
            padding: 20px;
            box-shadow: var(--shadow);
        }

        .metric-label {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
            margin-bottom: 6px;
        }

        .metric-value {
            font-family: var(--font-mono);
            font-size: 22px;
            font-weight: 600;
        }

        .metric-sub {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 2px;
        }

        /* Chart */
        .chart-container {
            background: var(--surface);
            border-radius: var(--radius);
            padding: 24px;
            box-shadow: var(--shadow);
            margin-bottom: 24px;
        }

        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }

        .chart-title {
            font-family: var(--font-display);
            font-size: 20px;
        }

        .period-tabs {
            display: flex;
            gap: 4px;
            background: var(--surface-dim);
            border-radius: 8px;
            padding: 3px;
        }

        .period-tab {
            font-family: var(--font-mono);
            font-size: 12px;
            font-weight: 500;
            padding: 5px 12px;
            border-radius: 6px;
            border: none;
            background: transparent;
            cursor: pointer;
            color: var(--text-muted);
            transition: all 0.15s;
        }
        .period-tab.active {
            background: var(--surface);
            color: var(--text);
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }

        .chart-canvas-wrap {
            position: relative;
            height: 300px;
        }

        /* DCA Section */
        .dca-section {
            background: var(--surface);
            border-radius: var(--radius);
            padding: 32px;
            box-shadow: var(--shadow);
            margin-bottom: 24px;
        }

        .dca-section h2 {
            font-family: var(--font-display);
            font-size: 20px;
            margin-bottom: 16px;
        }

        .dca-stats {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
            gap: 16px;
        }

        .dca-stat {
            padding: 16px;
            background: var(--surface-dim);
            border-radius: 8px;
        }

        .dca-stat-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
            margin-bottom: 4px;
        }

        .dca-stat-value {
            font-family: var(--font-mono);
            font-size: 20px;
            font-weight: 600;
        }

        /* Dividends Table */
        .div-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }

        .div-table th {
            text-align: left;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
            padding: 8px 12px;
            border-bottom: 2px solid var(--border);
        }

        .div-table td {
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
            font-family: var(--font-mono);
            font-size: 13px;
        }

        .div-table tr:last-child td { border-bottom: none; }

        /* Add ETF modal */
        .add-etf-bar {
            display: flex; gap: 8px; align-items: center;
            margin-bottom: 24px;
        }
        .add-etf-bar input {
            font-family: var(--font-mono);
            font-size: 14px;
            padding: 8px 14px;
            border: 1.5px solid var(--border);
            border-radius: 8px;
            background: var(--surface);
            width: 180px;
        }
        .add-etf-bar input:focus { outline: none; border-color: var(--text); }

        /* Loading */
        .loading {
            text-align: center;
            padding: 60px;
            color: var(--text-muted);
            font-size: 15px;
        }
        .loading::after {
            content: '';
            display: block;
            width: 24px; height: 24px;
            margin: 16px auto 0;
            border: 2.5px solid var(--border);
            border-top-color: var(--text);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        .disclaimer {
            text-align: center;
            color: var(--text-faint);
            font-size: 12px;
            padding: 32px 0 16px;
            border-top: 1px solid var(--border);
            margin-top: 32px;
        }

        @media (max-width: 640px) {
            .container { padding: 20px 16px; }
            .price-big { font-size: 28px; }
            .metrics-grid { grid-template-columns: repeat(2, 1fr); }
            .dca-stats { grid-template-columns: repeat(2, 1fr); }
            header { flex-direction: column; gap: 12px; align-items: flex-start; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>ETF Advisor</h1>
                <div class="subtitle" id="lastUpdated">Loading...</div>
            </div>
            <div class="actions">
                <label class="theme-switch">
                    <input type="checkbox" id="themeToggle" onchange="toggleTheme()">
                    <span class="theme-switch-label">PFD</span>
                </label>
                <button class="btn" onclick="triggerFetch()" id="fetchBtn">↻ Refresh Data</button>
            </div>
        </header>

        <div class="add-etf-bar">
            <input type="text" id="newTicker" placeholder="Add ticker (e.g. VAS)" onkeydown="if(event.key==='Enter')addETF()">
            <button class="btn btn-primary" onclick="addETF()">+ Add ETF</button>
        </div>

        <div id="dashboard">
            <div class="loading">Loading your ETF data...</div>
        </div>

        <p class="disclaimer">
            This is factual market data for personal use only — not financial advice.
            Data sourced from Yahoo Finance. Prices may be delayed.
        </p>
    </div>

    <script>
    const API = '';
    let charts = {};
    let activePeriods = {};

    // Theme support
    function toggleTheme() {
        const isPFD = document.getElementById('themeToggle').checked;
        document.documentElement.setAttribute('data-theme', isPFD ? 'pfd' : '');
        localStorage.setItem('etf-theme', isPFD ? 'pfd' : 'classic');
        reloadAllCharts();
    }

    function getChartColors() {
        const isPFD = document.documentElement.getAttribute('data-theme') === 'pfd';
        return isPFD ? {
            price: '#00e5ff', priceFill: 'rgba(0,229,255,0.06)',
            sma50: '#00ff41', sma200: '#ffbf00',
            grid: 'rgba(0,229,255,0.06)', gridX: 'rgba(0,229,255,0.06)',
            tick: '#5a6a7a',
            tooltip: '#1a2230', tooltipBorder: '#00e5ff', tooltipTitle: '#00e5ff', tooltipBody: '#d0d0d0',
            legendColor: '#5a6a7a',
            font: "'Share Tech Mono'",
            cornerRadius: 0,
        } : {
            price: '#1a1a1a', priceFill: 'rgba(26,26,26,0.04)',
            sma50: '#2d6a4f', sma200: '#c1292e',
            grid: '#f0efe9', gridX: 'transparent',
            tick: '#aaa',
            tooltip: '#1a1a1a', tooltipBorder: 'transparent', tooltipTitle: '#fff', tooltipBody: '#fff',
            legendColor: '#777',
            font: "'JetBrains Mono'",
            cornerRadius: 8,
        };
    }

    async function reloadAllCharts() {
        for (const ticker of Object.keys(activePeriods)) {
            await loadChart(ticker, activePeriods[ticker]);
        }
    }

    // Restore saved theme on page load
    (function() {
        const saved = localStorage.getItem('etf-theme');
        if (saved === 'pfd') {
            document.documentElement.setAttribute('data-theme', 'pfd');
            const toggle = document.getElementById('themeToggle');
            if (toggle) toggle.checked = true;
        }
    })();

    async function loadDashboard() {
        const etfsRes = await fetch(`${API}/api/etfs`);
        const etfs = await etfsRes.json();

        if (etfs.length === 0) {
            document.getElementById('dashboard').innerHTML = `
                <div class="loading" style="padding:40px;">
                    No ETFs tracked yet. Add a ticker above to get started!
                </div>`;
            return;
        }

        let html = '';
        for (const etf of etfs) {
            html += await renderETF(etf.ticker);
        }
        document.getElementById('dashboard').innerHTML = html;

        // Init charts
        for (const etf of etfs) {
            await loadChart(etf.ticker, 12);
        }
    }

    async function renderETF(ticker) {
        const [snapRes, dcaRes, divsRes] = await Promise.all([
            fetch(`${API}/api/snapshot/${ticker}`),
            fetch(`${API}/api/dca/${ticker}?amount=${MONTHLY_AMOUNT}&years=3`),
            fetch(`${API}/api/dividends/${ticker}`),
        ]);

        const snap = await snapRes.json();
        const dca = await dcaRes.json();
        const divs = await divsRes.json();

        if (snap.error) return `<div class="price-hero"><p>No data for ${ticker}</p></div>`;

        const changeClass = snap.daily_change >= 0 ? 'positive' : 'negative';
        const changeSign = snap.daily_change >= 0 ? '+' : '';
        const trendClass = `trend-${snap.trend_signal}`;
        const shortTicker = ticker.replace('.AX', '');

        document.getElementById('lastUpdated').textContent =
            `Last updated: ${snap.latest_date}`;

        let html = `
        <!-- Price Hero -->
        <div class="price-hero">
            <div class="ticker-row">
                <div>
                    <div class="ticker-name">${shortTicker}</div>
                    <div class="ticker-meta">${snap.name || ticker}</div>
                    <span class="trend-badge ${trendClass}">${snap.trend_signal}</span>
                </div>
                <div class="price-display">
                    <div class="price-big">$${snap.latest_price.toFixed(2)}</div>
                    <div class="price-change ${changeClass}">
                        ${changeSign}${snap.daily_change.toFixed(2)} (${changeSign}${snap.daily_change_pct.toFixed(2)}%)
                    </div>
                </div>
            </div>
        </div>

        <!-- Key Metrics -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">SMA-50</div>
                <div class="metric-value">${snap.sma_50 ? '$' + snap.sma_50.toFixed(2) : '—'}</div>
                <div class="metric-sub ${snap.price_vs_sma50 >= 0 ? 'positive' : 'negative'}">
                    ${snap.price_vs_sma50 ? (snap.price_vs_sma50 > 0 ? '+' : '') + snap.price_vs_sma50.toFixed(2) + '% from price' : ''}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">SMA-200</div>
                <div class="metric-value">${snap.sma_200 ? '$' + snap.sma_200.toFixed(2) : '—'}</div>
                <div class="metric-sub ${snap.price_vs_sma200 >= 0 ? 'positive' : 'negative'}">
                    ${snap.price_vs_sma200 ? (snap.price_vs_sma200 > 0 ? '+' : '') + snap.price_vs_sma200.toFixed(2) + '% from price' : ''}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">1Y Return</div>
                <div class="metric-value ${snap.return_1y >= 0 ? 'positive' : 'negative'}">
                    ${snap.return_1y != null ? (snap.return_1y > 0 ? '+' : '') + snap.return_1y.toFixed(2) + '%' : '—'}
                </div>
                <div class="metric-sub">YTD: ${snap.return_ytd != null ? (snap.return_ytd > 0 ? '+' : '') + snap.return_ytd.toFixed(2) + '%' : '—'}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Trailing Yield</div>
                <div class="metric-value">${snap.trailing_yield != null ? snap.trailing_yield.toFixed(2) + '%' : '—'}</div>
                <div class="metric-sub">12-month distributions</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Volatility</div>
                <div class="metric-value">${snap.volatility_annual != null ? snap.volatility_annual.toFixed(1) + '%' : '—'}</div>
                <div class="metric-sub">Annualised</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Sharpe Ratio</div>
                <div class="metric-value">${snap.sharpe_ratio != null ? snap.sharpe_ratio.toFixed(2) : '—'}</div>
                <div class="metric-sub">Risk-adj. return</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Max Drawdown</div>
                <div class="metric-value negative">${snap.max_drawdown != null ? snap.max_drawdown.toFixed(1) + '%' : '—'}</div>
                <div class="metric-sub">Worst peak-to-trough</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Expense Ratio</div>
                <div class="metric-value">${snap.expense_ratio != null ? (snap.expense_ratio * 100).toFixed(2) + '%' : '—'}</div>
                <div class="metric-sub">Annual fee</div>
            </div>
        </div>

        <!-- Price Chart -->
        <div class="chart-container">
            <div class="chart-header">
                <div class="chart-title">Price & Moving Averages</div>
                <div class="period-tabs">
                    <button class="period-tab" onclick="loadChart('${ticker}', 3)" data-ticker="${ticker}" data-months="3">3M</button>
                    <button class="period-tab" onclick="loadChart('${ticker}', 6)" data-ticker="${ticker}" data-months="6">6M</button>
                    <button class="period-tab active" onclick="loadChart('${ticker}', 12)" data-ticker="${ticker}" data-months="12">1Y</button>
                    <button class="period-tab" onclick="loadChart('${ticker}', 36)" data-ticker="${ticker}" data-months="36">3Y</button>
                    <button class="period-tab" onclick="loadChart('${ticker}', 60)" data-ticker="${ticker}" data-months="60">5Y</button>
                </div>
            </div>
            <div class="chart-canvas-wrap">
                <canvas id="chart-${shortTicker}"></canvas>
            </div>
        </div>`;

        // DCA Section
        if (dca && dca.total_invested) {
            const returnClass = dca.capital_return >= 0 ? 'positive' : 'negative';
            html += `
            <div class="dca-section">
                <h2>Dollar-Cost Averaging · $${MONTHLY_AMOUNT}/month · 3 Years</h2>
                <div class="dca-stats">
                    <div class="dca-stat">
                        <div class="dca-stat-label">Total Invested</div>
                        <div class="dca-stat-value">$${dca.total_invested.toLocaleString()}</div>
                    </div>
                    <div class="dca-stat">
                        <div class="dca-stat-label">Current Value</div>
                        <div class="dca-stat-value">$${dca.current_value.toLocaleString()}</div>
                    </div>
                    <div class="dca-stat">
                        <div class="dca-stat-label">Return</div>
                        <div class="dca-stat-value ${returnClass}">
                            ${dca.capital_return_pct > 0 ? '+' : ''}${dca.capital_return_pct.toFixed(1)}%
                        </div>
                    </div>
                    <div class="dca-stat">
                        <div class="dca-stat-label">Avg Cost Basis</div>
                        <div class="dca-stat-value">$${dca.average_cost.toFixed(2)}</div>
                    </div>
                    <div class="dca-stat">
                        <div class="dca-stat-label">Units Held</div>
                        <div class="dca-stat-value">${dca.total_units.toFixed(2)}</div>
                    </div>
                    <div class="dca-stat">
                        <div class="dca-stat-label">Est. Dividends</div>
                        <div class="dca-stat-value">$${dca.estimated_dividends.toFixed(2)}</div>
                    </div>
                </div>
            </div>`;
        }

        // Recent Dividends
        if (divs && divs.length > 0) {
            const recentDivs = divs.slice(0, 8);
            html += `
            <div class="chart-container">
                <div class="chart-title" style="margin-bottom:16px;">Recent Distributions</div>
                <table class="div-table">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Amount</th>
                            <th>Price</th>
                            <th>Yield</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${recentDivs.map(d => `
                        <tr>
                            <td>${d.date}</td>
                            <td>$${d.amount.toFixed(4)}</td>
                            <td>$${d.price_on_date.toFixed(2)}</td>
                            <td>${d.yield_pct.toFixed(3)}%</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>`;
        }

        return html;
    }

    const MONTHLY_AMOUNT = """ + str(MONTHLY_AMOUNT) + """;

    async function loadChart(ticker, months) {
        const shortTicker = ticker.replace('.AX', '');
        const res = await fetch(`${API}/api/chart/${ticker}?months=${months}`);
        const data = await res.json();

        activePeriods[ticker] = months;

        // Update active tab
        document.querySelectorAll(`.period-tab[data-ticker="${ticker}"]`).forEach(t => {
            t.classList.toggle('active', parseInt(t.dataset.months) === months);
        });

        if (charts[ticker]) charts[ticker].destroy();

        const ctx = document.getElementById(`chart-${shortTicker}`);
        if (!ctx) return;

        const c = getChartColors();

        charts[ticker] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [
                    {
                        label: 'Price',
                        data: data.prices,
                        borderColor: c.price,
                        backgroundColor: c.priceFill,
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.1,
                    },
                    {
                        label: 'SMA-50',
                        data: data.sma50,
                        borderColor: c.sma50,
                        borderWidth: 1.5,
                        borderDash: [6, 3],
                        pointRadius: 0,
                        fill: false,
                    },
                    {
                        label: 'SMA-200',
                        data: data.sma200,
                        borderColor: c.sma200,
                        borderWidth: 1.5,
                        borderDash: [6, 3],
                        pointRadius: 0,
                        fill: false,
                    },
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    intersect: false,
                    mode: 'index',
                },
                plugins: {
                    legend: {
                        position: 'top',
                        align: 'end',
                        labels: {
                            usePointStyle: true,
                            pointStyle: 'line',
                            color: c.legendColor,
                            font: { family: c.font, size: 12 },
                            padding: 16,
                        }
                    },
                    tooltip: {
                        backgroundColor: c.tooltip,
                        borderColor: c.tooltipBorder,
                        borderWidth: c.tooltipBorder === 'transparent' ? 0 : 1,
                        titleFont: { family: c.font, size: 12 },
                        titleColor: c.tooltipTitle,
                        bodyFont: { family: c.font, size: 12 },
                        bodyColor: c.tooltipBody,
                        padding: 12,
                        cornerRadius: c.cornerRadius,
                        displayColors: true,
                        callbacks: {
                            label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y?.toFixed(2) || '—'}`,
                        },
                    },
                },
                scales: {
                    x: {
                        display: true,
                        grid: { color: c.gridX },
                        ticks: {
                            maxTicksLimit: 8,
                            font: { family: c.font, size: 11 },
                            color: c.tick,
                        },
                    },
                    y: {
                        display: true,
                        grid: { color: c.grid },
                        ticks: {
                            font: { family: c.font, size: 11 },
                            color: c.tick,
                            callback: v => '$' + v.toFixed(0),
                        },
                    },
                },
            }
        });
    }

    async function triggerFetch() {
        const btn = document.getElementById('fetchBtn');
        btn.textContent = '⏳ Fetching...';
        btn.disabled = true;
        try {
            await fetch(`${API}/api/fetch`, { method: 'POST' });
            await loadDashboard();
        } catch (e) {
            alert('Fetch failed: ' + e.message);
        }
        btn.textContent = '↻ Refresh Data';
        btn.disabled = false;
    }

    async function addETF() {
        const input = document.getElementById('newTicker');
        const ticker = input.value.trim().toUpperCase();
        if (!ticker) return;

        input.disabled = true;
        try {
            const res = await fetch(`${API}/api/add-etf?ticker=${encodeURIComponent(ticker)}`, { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success' || data.status === 'exists') {
                input.value = '';
                await loadDashboard();
            } else {
                alert('Could not add ' + ticker + ': ' + (data.message || 'Unknown error'));
            }
        } catch (e) {
            alert('Error: ' + e.message);
        }
        input.disabled = false;
    }

    loadDashboard();
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML
