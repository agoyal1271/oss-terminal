# OSS Terminal

A free, open-data equity research web app for US public companies — the "can I
make a good investment decision" slice of a Bloomberg Terminal, built entirely
on public data with no paid dependencies. Every number traces back to a live
SEC filing.

This is Phase 1 of a larger plan (see "Roadmap" below): a company research
page with normalized financial statements, price history, and a filing
browser. Scope: **US-listed companies only, web UI only.**

## What it does

Search any US public company (10,400+ SEC filers) and get:

- **Normalized financial statements** — 8+ years of income statement, balance
  sheet, and cash flow data, pulled live from SEC XBRL and normalized across
  the dozens of different tags companies use to report the same line item.
- **Computed ratios** — margins, ROE, ROA, current ratio, debt/equity, free
  cash flow — computed from the normalized data, not separately sourced (so
  they can't silently disagree with the statements above them).
- **Price chart** — 1M/6M/1Y/5Y/Max, delayed EOD/intraday.
- **Filing browser** — recent SEC filings with direct links to EDGAR,
  defaulting to substantive filing types (10-K/10-Q/8-K, proxies, insider
  Form 4, beneficial ownership) with a toggle to see everything.
- **Source transparency** — a "show XBRL source tags" toggle on the financials
  table reveals exactly which reported GAAP tag each row's numbers came from.

## Architecture

```
frontend/   Vite + React + TypeScript, Recharts for charts, react-router
backend/    FastAPI (Python), no database — on-demand fetch with disk cache
```

There's no bulk-ingested data warehouse in this phase. Each company's data is
fetched live from SEC/Yahoo on first request and cached to disk as flat JSON
(`backend/data/cache/`) with a TTL per source (12h for fundamentals, 15min for
prices). This keeps the project runnable with zero infra — no Postgres, no
ingestion pipeline to babysit — at the cost of not supporting cross-company
screening yet (that's Phase 2, see below).

### Backend layout

- `app/core/metric_map.py` — the canonical-metric → candidate-XBRL-tags table.
  This is the accuracy-critical part: different filers (and the same filer
  across years) report "revenue" under different tags
  (`RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`,
  `SalesRevenueNet`, ...). Each canonical metric lists candidates in priority
  order; the normalizer takes the first one with usable data.
- `app/ingest/financials.py` — fetches SEC XBRL company facts and normalizes
  them: picks 10-K/FY annual facts, validates flow-metric durations are a full
  fiscal year (300–400 days, to exclude stub periods from fiscal-year
  changes), and prefers the most-recently-filed value per fiscal year end (so
  restatements win over originally-reported figures).
- `app/ingest/prices.py` — Yahoo Finance's unofficial chart endpoint.
- `app/ingest/filings.py` — SEC submissions API (company profile + filing
  list).
- `app/ingest/tickers.py` — SEC's own ticker↔CIK mapping (search index).

## Data sources — what's used and why

| Data | Source | Notes |
|---|---|---|
| Financial statements | [SEC EDGAR XBRL company facts API](https://www.sec.gov/os/webmaster-faq#developers) | Official, free, no key. Every US public filer back to ~2009. This is ground truth — the same data Bloomberg's FA screen is built on. |
| Filings list, SIC/exchange | [SEC submissions API](https://www.sec.gov/os/webmaster-faq#developers) | Official, free, no key. |
| Ticker↔CIK search index | [SEC company_tickers.json](https://www.sec.gov/files/company_tickers.json) | Official, free, no key. ~10,400 entities. |
| Prices | Yahoo Finance unofficial chart endpoint | **No key, but unofficial** — same endpoint the `yfinance` library uses. No published rate limit or SLA; Yahoo could change or block it. Delayed, not a licensed real-time feed. See "Known limitations." |

**Dropped during build:** Stooq was in the original plan for prices but now
requires solving a JavaScript proof-of-work challenge to access
programmatically — not scriptable without a headless browser, so it was
replaced with Yahoo's endpoint.

## Accuracy notes (read this before trusting a number)

- **Fiscal year labels are derived from the period end date**, not from SEC's
  own `fy` field. SEC's XBRL `fy` field is the *filing's* fiscal-year focus,
  not the period's — a 10-K covering fiscal 2025 tags its FY2023 and FY2024
  comparative figures with `fy=2025` too, which would mislabel every
  comparative year if used directly. This was caught and fixed during
  validation (see git history).
- **Restatements**: when a fiscal year's figures appear in multiple filings
  (e.g., as a comparative in the following year's 10-K), the most recently
  filed value wins. You're seeing the latest-known figure, not necessarily
  what was originally reported.
- **Banks/financials render partial statements** — `current_assets`,
  `current_liabilities`, and similar classified-balance-sheet tags don't
  apply to banks (assets aren't split current/noncurrent under GAAP for
  financial institutions), so those cells show "—" rather than a wrong
  number. This is correct, not a bug.
- **All figures validated against known-correct values** for AAPL, MSFT, and
  JPM during development (revenue, net income, EPS, margins all matched
  public figures) — see the checks run during this build.
- **Filing list defaults to substantive forms** (10-K, 10-Q, 8-K, proxies,
  Form 4, 13D/G). Frequent debt issuers (banks especially) file hundreds of
  routine `424B2`/`FWP` prospectus supplements a year; without this filter
  they bury the filings that actually matter. Toggle "Show all filing types"
  to see the unfiltered list.
- **Market cap and P/E are approximate**: computed client-side from
  `price × last-reported diluted shares outstanding`, which can lag the
  actual current share count by up to a year (balance-sheet date, not
  today).

## Known limitations

- **Prices are not a licensed feed.** Real-time consolidated market data
  requires exchange licensing agreements (NYSE/Nasdaq) with per-user
  reporting and non-display fees — that's a wall every free project like this
  hits. Yahoo's endpoint is free and keyless but unofficial, delayed, and
  could break without notice.
- **No cross-company screening yet.** Because data is fetched on-demand per
  ticker rather than bulk-ingested, there's no "find all companies with ROE >
  20%" query. That needs a real ingestion pipeline (Phase 2).
- **No analyst consensus estimates.** Those are proprietary
  (Refinitiv/Visible Alpha/FactSet) — no free equivalent exists.
- **No insider trading (Form 4) detail or institutional ownership (13F)
  parsing** — the filing browser links to these filings on EDGAR, but doesn't
  parse and normalize their contents yet.
- **Quarterly financials aren't normalized**, only annual (10-K/FY). Adding
  10-Q normalization is straightforward given the existing tag-mapping
  infrastructure but was left out of this pass to keep scope tight.

## Running it

Two servers, no database, no signup required for the data sources used.

```bash
# backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## Roadmap (not built yet)

This is Phase 1 of the plan discussed. Later phases:

- **Phase 2 — Screener + macro.** Requires bulk XBRL ingestion (SEC's daily
  bulk `companyfacts.zip`) into a real datastore (Postgres/DuckDB) so queries
  can run across the whole universe instead of one ticker at a time. Add FRED
  macro series.
- **Phase 3 — Filing intelligence.** RAG over the full 10-K/10-Q corpus with
  citation-locked answers, and an automatic filing-diff engine (red-line this
  10-K against last year's, surface changed risk-factor language).
- **Phase 4 — Valuation tools.** Reverse-DCF, scenario models, event
  monitoring/alerts on new filings or insider activity.

## Legal/compliance notes

- This is a research tool: it surfaces sourced data, not recommendations. It
  deliberately never outputs a buy/sell signal or position sizing — doing so
  would risk crossing into investment-adviser territory.
- The Yahoo Finance price endpoint is unofficial; treat this as a
  personal/research tool, not a redistribution service. Swap in a licensed
  feed (Tiingo, Polygon, IEX) before using this commercially.
- SEC's fair-access policy requires a descriptive `User-Agent` with contact
  info on every request (see `backend/app/config.py`) and asks callers not to
  exceed ~10 requests/second — this project caches aggressively specifically
  to stay well under that.
