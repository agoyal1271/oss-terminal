"""Rule engine for the daily unusual-activity scanner.

Compares today's computed signals against yesterday's persisted scan-state
(read live from GitHub raw content, same pattern as get_iv_rank) and emits
finding dicts for whatever changed in a way worth alerting on.

The single most important design rule here, and the reason this file
exists rather than just reusing the UI's describe_* functions directly:
**fire on transitions, not states.** "AAPL is in an uptrend" every single
day is noise nobody reads after week one; "AAPL's death cross just
occurred" is the one day that actually matters. Every signal below is
either inherently a same-day event (a new 52-week high, a filing dated
today) or explicitly diffed against a prior value read from state.

Finding shape (mirrors the market-intel house style):
{key, ticker, category, severity, title, detail, value, ctx}
- key: stable fingerprint for cooldown/dedup, e.g. "AAPL.golden_cross"
- severity: "critical" | "warn" | "info"
"""

from __future__ import annotations

import datetime

from app.config import settings
from app.core.http_cache import UpstreamError, cached_get_json
from app.ingest import filings as filings_ingest
from app.ingest import options as options_ingest
from app.ingest import prices as prices_ingest
from app.ingest import tickers as tickers_ingest
from app.signals.options_signals import describe_skew, describe_term_structure
from app.signals.technicals import compute_technical_read

SCAN_STATE_RAW_BASE = "https://raw.githubusercontent.com/agoyal1271/oss-terminal/main/data/scan-state"

CRITICAL_8K_ITEMS = {"1.03", "4.02", "3.01", "1.05"}  # bankruptcy, restatement, delisting, cyber
MATERIAL_8K_ITEMS = {"2.06", "5.02", "5.01", "2.01"}  # impairment, exec change, control change, M&A
OWNERSHIP_FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}


def _finding(key: str, ticker: str, category: str, severity: str, title: str, detail: str, value=None, ctx: dict | None = None) -> dict:
    return {
        "key": key,
        "ticker": ticker,
        "category": category,
        "severity": severity,
        "title": title,
        "detail": detail,
        "value": value,
        "ctx": ctx or {},
    }


def _load_prior_state(ticker: str) -> dict:
    url = f"{SCAN_STATE_RAW_BASE}/{ticker.upper()}.json"
    try:
        data = cached_get_json(namespace="scan_state", url=url, ttl=6 * 3600, headers={"User-Agent": "OSS-Terminal"})
        return data if isinstance(data, dict) else {}
    except UpstreamError:
        return {}  # no prior state yet (first run for this ticker) -- not an error


def _load_iv_history_tail(ticker: str) -> dict | None:
    url = f"{settings.iv_history_repo_raw_base}/{ticker.upper()}.json"
    try:
        history = cached_get_json(namespace="iv_history", url=url, ttl=6 * 3600, headers={"User-Agent": "OSS-Terminal"})
    except UpstreamError:
        return None
    return history[-1] if isinstance(history, list) and history else None


def _scan_technicals(ticker: str, findings: list[dict], errors: list[str]) -> None:
    try:
        price_history = prices_ingest.get_price_history(ticker, range_key="2y")
        tech = compute_technical_read(price_history["points"])
    except (UpstreamError, ValueError) as exc:
        errors.append(f"prices: {exc}")
        return
    if tech is None:
        errors.append("prices: not enough history for technicals")
        return

    if None not in (tech.sma50, tech.sma200, tech.sma50_prev, tech.sma200_prev):
        crossed_up = tech.sma50_prev <= tech.sma200_prev and tech.sma50 > tech.sma200
        crossed_down = tech.sma50_prev >= tech.sma200_prev and tech.sma50 < tech.sma200
        if crossed_up:
            findings.append(_finding(
                f"{ticker}.golden_cross", ticker, "technical", "warn",
                f"{ticker}: golden cross",
                "50-day SMA just crossed above the 200-day SMA -- bullish trend structure forming.",
                value=tech.sma50, ctx={"sma50": tech.sma50, "sma200": tech.sma200},
            ))
        elif crossed_down:
            findings.append(_finding(
                f"{ticker}.death_cross", ticker, "technical", "warn",
                f"{ticker}: death cross",
                "50-day SMA just crossed below the 200-day SMA -- bearish trend structure forming.",
                value=tech.sma50, ctx={"sma50": tech.sma50, "sma200": tech.sma200},
            ))

    if tech.rsi14 is not None and tech.rsi14_prev is not None:
        if tech.rsi14 >= 70 and tech.rsi14_prev < 70:
            findings.append(_finding(
                f"{ticker}.rsi_overbought", ticker, "technical", "info",
                f"{ticker}: RSI crossed into overbought", f"RSI just crossed above 70 (now {tech.rsi14:.0f}).",
                value=tech.rsi14,
            ))
        elif tech.rsi14 <= 30 and tech.rsi14_prev > 30:
            findings.append(_finding(
                f"{ticker}.rsi_oversold", ticker, "technical", "info",
                f"{ticker}: RSI crossed into oversold", f"RSI just crossed below 30 (now {tech.rsi14:.0f}).",
                value=tech.rsi14,
            ))

    # Window excludes today's own bar (see technicals.py), so this is a real breakout.
    if tech.high_52w_prior is not None and tech.latest_close > tech.high_52w_prior:
        findings.append(_finding(
            f"{ticker}.new_52w_high", ticker, "technical", "warn",
            f"{ticker}: new 52-week high",
            f"Closed at {tech.latest_close:.2f}, above the prior 52-week high of {tech.high_52w_prior:.2f}.",
            value=tech.latest_close,
        ))
    if tech.low_52w_prior is not None and tech.latest_close < tech.low_52w_prior:
        findings.append(_finding(
            f"{ticker}.new_52w_low", ticker, "technical", "warn",
            f"{ticker}: new 52-week low",
            f"Closed at {tech.latest_close:.2f}, below the prior 52-week low of {tech.low_52w_prior:.2f}.",
            value=tech.latest_close,
        ))

    if tech.volume_ratio is not None and tech.volume_ratio >= 2.0:
        findings.append(_finding(
            f"{ticker}.volume_spike", ticker, "technical", "info",
            f"{ticker}: volume spike",
            f"10-day average volume is {tech.volume_ratio:.1f}x the prior 50-day baseline.",
            value=tech.volume_ratio,
        ))


def _scan_options(ticker: str, prior: dict, today_state: dict, findings: list[dict], errors: list[str]) -> None:
    # Deliberately scan the ~30-day-tenor chain, not the nearest expiration.
    # Found by testing against real data: the nearest chain is often a
    # same-week expiring weekly, where volume dwarfing open interest is the
    # STRUCTURAL NORM (weeklies reset OI every week; almost the whole board
    # trips a naive "volume > OI" check), not an anomaly. It flagged 16
    # findings for one ticker on the first live test -- worthless noise that
    # would have buried every other ticker's real signal. A stable ~30-day
    # tenor is also more comparable day-to-day, same reasoning as
    # closest_expiration_to_days()'s use in get_iv_snapshot.
    try:
        nearest = options_ingest.get_options_chain(ticker)
        target_expiration = options_ingest.closest_expiration_to_days(nearest["expiration_dates"], 30)
        chain = (
            nearest if target_expiration is None or target_expiration == nearest["selected_expiration"]
            else options_ingest.get_options_chain(ticker, target_expiration)
        )
    except (UpstreamError, ValueError) as exc:
        errors.append(f"options chain: {exc}")
        return

    s = chain["summary"]

    # Unusual activity: require BOTH a large ratio (volume >= 3x open
    # interest, or real volume against a brand-new strike with ~0 OI) AND a
    # meaningful absolute size, so illiquid noise doesn't qualify. Capped to
    # the top 3 by volume per ticker -- a genuinely busy day for one name
    # still shouldn't crowd out every other ticker's findings.
    candidates = [
        c for c in chain["calls"] + chain["puts"]
        if c["volume"] >= 1000 and (c["open_interest"] == 0 or c["volume"] >= c["open_interest"] * 3)
    ]
    for c in sorted(candidates, key=lambda c: -c["volume"])[:3]:
        findings.append(_finding(
            f"{ticker}.unusual_activity.{c['contract_symbol']}", ticker, "options", "warn",
            f"{ticker}: unusual options activity",
            f"{c['contract_symbol']} (~30d tenor): volume {c['volume']:,} vs open interest {c['open_interest']:,} -- new positioning, not just today's expiring weekly noise.",
            value=c["volume"], ctx={"strike": c["strike"], "expiration": chain["selected_expiration"]},
        ))

    pc_ratio = s.get("put_call_volume_ratio")
    pc_history = list(prior.get("pc_ratio_history") or [])
    if pc_ratio is not None and len(pc_history) >= 5:
        baseline = sum(pc_history) / len(pc_history)
        if baseline > 0 and (pc_ratio > baseline * 1.5 or pc_ratio < baseline * 0.5):
            findings.append(_finding(
                f"{ticker}.pc_ratio_deviation", ticker, "options", "info",
                f"{ticker}: put/call ratio deviating from its own baseline",
                f"Put/call volume ratio is {pc_ratio:.2f} vs its own {len(pc_history)}-day average of {baseline:.2f}.",
                value=pc_ratio,
            ))
    if pc_ratio is not None:
        pc_history = (pc_history + [pc_ratio])[-10:]
    today_state["pc_ratio_history"] = pc_history

    try:
        ts = options_ingest.get_iv_term_structure(ticker)
        ts_read = describe_term_structure(ts["points"])
        was_event = bool(prior.get("term_structure_event"))
        if ts_read.has_event_signal and not was_event:
            findings.append(_finding(
                f"{ticker}.term_structure_backwardation", ticker, "options", "warn",
                f"{ticker}: IV term structure flipped into backwardation",
                ts_read.summary, ctx={"spike_expiration": ts_read.spike_expiration},
            ))
        today_state["term_structure_event"] = ts_read.has_event_signal
    except (UpstreamError, ValueError) as exc:
        errors.append(f"term structure: {exc}")

    skew_read = describe_skew(chain)
    if skew_read.direction == "inverted" and prior.get("skew_direction") != "inverted":
        findings.append(_finding(
            f"{ticker}.skew_inverted", ticker, "options", "warn",
            f"{ticker}: IV skew flipped inverted", skew_read.summary,
        ))
    today_state["skew_direction"] = skew_read.direction

    prev_iv_entry = _load_iv_history_tail(ticker)
    atm_call_iv, atm_put_iv = s.get("atm_call_iv"), s.get("atm_put_iv")
    if prev_iv_entry and atm_call_iv is not None:
        prev_iv = ((prev_iv_entry.get("call_iv") or 0) + (prev_iv_entry.get("put_iv") or 0)) / 2
        current_iv = ((atm_call_iv or 0) + (atm_put_iv or 0)) / 2
        if prev_iv > 0 and current_iv > prev_iv * 1.2:
            findings.append(_finding(
                f"{ticker}.iv_jump", ticker, "options", "warn",
                f"{ticker}: ATM IV jumped",
                f"ATM IV moved from {prev_iv * 100:.0f}% to {current_iv * 100:.0f}% day-over-day.",
                value=current_iv,
            ))

    try:
        rank = options_ingest.get_iv_rank(ticker)
        iv_rank = rank.get("iv_rank")
        if iv_rank is not None and iv_rank > 80:
            findings.append(_finding(
                f"{ticker}.iv_rank_high", ticker, "options", "info",
                f"{ticker}: IV rank extreme (high)",
                f"IV rank is {iv_rank:.0f} ({rank['history_days']} days of history) -- premium is rich relative to its own recent range.",
                value=iv_rank,
            ))
        elif iv_rank is not None and iv_rank < 20:
            findings.append(_finding(
                f"{ticker}.iv_rank_low", ticker, "options", "info",
                f"{ticker}: IV rank extreme (low)",
                f"IV rank is {iv_rank:.0f} ({rank['history_days']} days of history) -- premium is cheap relative to its own recent range.",
                value=iv_rank,
            ))
    except (UpstreamError, ValueError) as exc:
        errors.append(f"iv rank: {exc}")


def _scan_filings(ticker: str, findings: list[dict], errors: list[str]) -> None:
    row = tickers_ingest.resolve(ticker)
    if not row:
        errors.append("filings: ticker not found in SEC universe")
        return

    today_iso = datetime.date.today().isoformat()
    five_days_ago = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()

    try:
        eightks = filings_ingest.get_recent_filings(row["cik_str"], limit=25, forms=["8-K", "8-K/A"])
        for f in eightks:
            if f["filing_date"] != today_iso:
                continue
            items = set(f.get("items") or [])
            critical_hit = items & CRITICAL_8K_ITEMS
            material_hit = items & MATERIAL_8K_ITEMS
            if critical_hit:
                codes = ", ".join(sorted(critical_hit))
                findings.append(_finding(
                    f"{ticker}.8k.{f['accession_number']}", ticker, "filing", "critical",
                    f"{ticker}: critical 8-K filed ({codes})",
                    f"Item(s) {codes} filed today. {f['filing_index_url']}",
                    ctx={"accession": f["accession_number"], "items": sorted(items)},
                ))
            elif material_hit:
                codes = ", ".join(sorted(material_hit))
                findings.append(_finding(
                    f"{ticker}.8k.{f['accession_number']}", ticker, "filing", "warn",
                    f"{ticker}: material 8-K filed ({codes})",
                    f"Item(s) {codes} filed today. {f['filing_index_url']}",
                    ctx={"accession": f["accession_number"], "items": sorted(items)},
                ))
            elif "2.02" in items:
                findings.append(_finding(
                    f"{ticker}.8k.{f['accession_number']}", ticker, "filing", "info",
                    f"{ticker}: earnings 8-K filed", f"Results of Operations (2.02) filed today. {f['filing_index_url']}",
                    ctx={"accession": f["accession_number"]},
                ))
    except UpstreamError as exc:
        errors.append(f"8-K filings: {exc}")

    try:
        ownership_filings = filings_ingest.get_recent_filings(row["cik_str"], limit=10, forms=list(OWNERSHIP_FORMS))
        for f in ownership_filings:
            if f["filing_date"] == today_iso:
                findings.append(_finding(
                    f"{ticker}.ownership.{f['accession_number']}", ticker, "filing", "warn",
                    f"{ticker}: new {f['form']} filed",
                    f"New beneficial-ownership filing ({f['form']}) today -- possible activist stake or large position change. {f['filing_index_url']}",
                    ctx={"accession": f["accession_number"], "form": f["form"]},
                ))
    except UpstreamError as exc:
        errors.append(f"13D/G filings: {exc}")

    try:
        form4s = filings_ingest.get_recent_filings(row["cik_str"], limit=25, forms=["4"])
        recent_count = sum(1 for f in form4s if f["filing_date"] >= five_days_ago)
        if recent_count >= 3:
            findings.append(_finding(
                f"{ticker}.insider_cluster.{datetime.date.today().isocalendar()[1]}", ticker, "filing", "info",
                f"{ticker}: insider filing cluster",
                f"{recent_count} Form 4 filings in the last 5 days.",
                value=recent_count,
            ))
    except UpstreamError as exc:
        errors.append(f"Form 4 filings: {exc}")


def scan_ticker(ticker: str) -> dict:
    """Full scan for one ticker. Returns:
    {ticker, findings: [...], today_state: {...}, errors: [...]}

    `today_state` is what the caller (scripts/scan_signals.py) should
    persist to data/scan-state/{ticker}.json so tomorrow's run has
    something to diff against. `errors` is never silently swallowed --
    the caller surfaces it rather than treating a failed sub-scan as
    "nothing happened" (see get_watchlist_snapshot's silent per-ticker
    skip, which this deliberately does not repeat).
    """
    ticker = ticker.upper()
    findings: list[dict] = []
    errors: list[str] = []
    prior = _load_prior_state(ticker)
    today_state: dict = {"date": datetime.date.today().isoformat()}

    _scan_technicals(ticker, findings, errors)
    _scan_options(ticker, prior, today_state, findings, errors)
    _scan_filings(ticker, findings, errors)

    return {"ticker": ticker, "findings": findings, "today_state": today_state, "errors": errors}
