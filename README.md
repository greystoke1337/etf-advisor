# ETF Advisor 📊

A personal ETF monitoring dashboard built for Australian investors. Tracks ASX and global ETFs with price charts, moving averages, DCA projections, dividend tracking, risk metrics, and weekly email digests.

**This is a personal tool — not financial advice.**

---

## What it does

- **Daily data fetching** — Pulls price, dividend, and metadata from Yahoo Finance after ASX close (5 PM AEST)
- **Price charts** — Interactive charts with SMA-50 and SMA-200 moving averages
- **Trend signals** — Bullish/bearish/neutral based on price vs moving averages
- **DCA simulator** — Shows what $X/month would have returned over 1–5 years
- **Dividend tracking** — Distribution history with yield calculations
- **Risk metrics** — Volatility, Sharpe ratio, max drawdown
- **Weekly email digest** — Summary report every Sunday evening (AEST)
- **Add ETFs anytime** — Type a ticker to start tracking it

---

## Deploy to Railway (5 minutes)

### Step 1: Push to GitHub

Create a new GitHub repo and push this code:

```bash
cd etf-advisor
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/etf-advisor.git
git push -u origin main
```

### Step 2: Create Railway project

1. Go to [railway.app](https://railway.app) and log in
2. Click **"New Project"** → **"Deploy from GitHub Repo"**
3. Select your `etf-advisor` repo
4. Railway will auto-detect the Python app

### Step 3: Add PostgreSQL

1. In your Railway project, click **"+ New"** → **"Database"** → **"PostgreSQL"**
2. Railway automatically sets the `DATABASE_URL` environment variable — you don't need to do anything

### Step 4: Set environment variables

In your Railway service settings → **Variables**, add:

| Variable | Value |
|----------|-------|
| `DCA_MONTHLY_AMOUNT` | `200` (or your preferred amount) |
| `RESEND_API_KEY` | Your key from [resend.com](https://resend.com) (optional) |
| `EMAIL_TO` | Your email address (optional) |

### Step 5: Generate a domain

1. In your service → **Settings** → **Networking**
2. Click **"Generate Domain"**
3. You'll get a URL like `etf-advisor-production.up.railway.app`

### Step 6: Visit your dashboard!

Open your Railway domain. On first load, the app will fetch 5 years of DHHF history — this takes about 30 seconds. After that, it updates daily at 5 PM AEST.

---

## Setting up email reports (optional)

1. Sign up at [resend.com](https://resend.com) (free — 3,000 emails/month)
2. Get your API key from the Resend dashboard
3. Add the `RESEND_API_KEY` and `EMAIL_TO` variables in Railway
4. Emails send automatically every Sunday at 6 PM AEST
5. Preview your email anytime at `/api/email-preview`

**Note:** On Resend's free tier, you can only send to your own email. This is perfect for a personal tool.

---

## Adding more ETFs

Type any ticker in the "Add ticker" field on the dashboard:

- **ASX ETFs**: Just type the code — `VAS`, `VDHG`, `A200`, `IOZ`, `NDQ`, `VGS`
- **US ETFs**: Add the full ticker — `VOO`, `VTI`, `QQQ`
- The app auto-appends `.AX` for ASX tickers

Popular Australian ETFs worth tracking:

| Ticker | Name | Category |
|--------|------|----------|
| DHHF | BetaShares Diversified All Growth | Diversified |
| VAS | Vanguard Australian Shares | Australian |
| VGS | Vanguard Intl Shares | International |
| A200 | BetaShares Australia 200 | Australian |
| NDQ | BetaShares Nasdaq 100 | US Tech |
| VDHG | Vanguard Diversified High Growth | Diversified |

---

## API endpoints

All data is available via JSON APIs:

| Endpoint | Description |
|----------|-------------|
| `GET /api/etfs` | List tracked ETFs |
| `GET /api/snapshot/{ticker}` | Full metrics snapshot |
| `GET /api/chart/{ticker}?months=12` | Chart data |
| `GET /api/dividends/{ticker}` | Dividend history |
| `GET /api/dca/{ticker}?amount=200&years=3` | DCA projection |
| `POST /api/add-etf?ticker=VAS` | Add new ETF |
| `POST /api/fetch` | Trigger manual data refresh |
| `GET /api/email-preview` | Preview weekly email |
| `GET /api/logs` | Fetch operation logs |

---

## How it works

```
┌─────────────────────────────────────────────┐
│  Dashboard (HTML + Chart.js)                │
│  Served by FastAPI at /                     │
└──────────────┬──────────────────────────────┘
               │ REST API calls
┌──────────────▼──────────────────────────────┐
│  FastAPI Backend                            │
│  + APScheduler (daily fetch, weekly email)  │
│  + Analysis engine (SMA, DCA, risk)         │
└──────────────┬──────────────────────────────┘
               │ SQLAlchemy
┌──────────────▼──────────────────────────────┐
│  PostgreSQL (Railway)                       │
│  ~5-10 MB for 20 ETFs × 10 years           │
└─────────────────────────────────────────────┘

Data flow:
1. APScheduler triggers yfinance fetch at 07:00 UTC (5 PM AEST)
2. New price data stored in PostgreSQL (incremental)
3. Dashboard reads from DB via API and renders charts
4. Weekly email generated from same data, sent via Resend
```

---

## Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run with SQLite (no Postgres needed locally)
# Just don't set DATABASE_URL — it defaults to SQLite
uvicorn main:app --reload --port 8000

# Open http://localhost:8000
```

---

## Cost

- **Railway Hobby Plan**: ~$5/month (this small app typically uses $1-2 of compute)
- **Resend**: Free (3,000 emails/month)
- **Yahoo Finance data**: Free (via yfinance library)

---

## Disclaimer

This tool displays factual market data and mathematical calculations for personal use only. It does not constitute financial advice. Always do your own research and consider consulting a licensed financial adviser before making investment decisions.
