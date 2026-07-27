#!/usr/bin/env python3
"""Daily unusual-activity scan driver, called by .github/workflows/daily-scan.yml
(Layer 1 of the two-layer design -- see README.md "Daily scanner").

Loops the watchlist, calls the backend's per-ticker scan endpoint
(app/signals/detect.py does the actual rule evaluation), applies severity
gating + per-(ticker,signal) cooldown, writes the day's findings to
data/scans/{date}.json + latest.json and today's per-ticker state to
data/scan-state/{ticker}.json (so tomorrow's backend call has something to
diff a transition against), and posts a plain-text baseline digest to
Slack via incoming webhook -- this posts regardless of whether the Layer 2
agent triage ever runs, so alerting doesn't have a single point of failure.

Stdlib only, matching scripts/snapshot_iv.py -- the workflow has no `pip
install` step.

Usage: python scripts/scan_signals.py [--dry] [--force]
  --dry    compute and print, write nothing, post nothing
  --force  ignore cooldowns (useful for testing that delivery works at all)
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_URL = os.environ.get("BACKEND_URL", "https://backend-gules-iota-44.vercel.app")
SECRET = os.environ.get("IV_SNAPSHOT_SECRET", "")  # same trust boundary as the IV snapshot endpoint
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# Base watchlist: IV_WATCHLIST (backend/app/ingest/options.py) + the
# tickers tracked in ~/stock-pivot-tracker, per the approved plan. NUGT (a
# Direxion leveraged ETF) was in the original tracker list but is dropped
# here: it isn't in SEC's company_tickers.json at all (that file covers
# operating-company filers, not most ETFs -- confirmed live,
# /api/search?q=NUGT returns zero results and /api/companies/NUGT 404s), so
# it's not resolvable by ANY endpoint in this app today, not just scanning.
# Watchlisting a ticker that can never succeed would just be a permanent
# phantom failure in the daily digest -- the exact kind of noise this
# scanner exists to avoid.
BASE_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "JPM", "BRK-B", "BMNR", "COIN"]

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANS_DIR = REPO_ROOT / "data" / "scans"
SCAN_STATE_DIR = REPO_ROOT / "data" / "scan-state"
COOLDOWN_PATH = SCAN_STATE_DIR / "cooldowns.json"
WATCHLIST_PATH = REPO_ROOT / "data" / "watchlist.json"


def load_watchlist() -> list[str]:
    """Base list plus anything scripts/ask.py registered when someone asked
    to analyze a ticker not already covered -- so "analyze SNOW" once means
    SNOW gets a daily scan from then on, not just today's one-off lookup."""
    extra: list[str] = []
    if WATCHLIST_PATH.exists():
        try:
            data = json.loads(WATCHLIST_PATH.read_text())
            if isinstance(data, list):
                extra = [t.upper() for t in data]
        except json.JSONDecodeError:
            pass
    return sorted(set(BASE_WATCHLIST) | set(extra))


WATCHLIST = load_watchlist()

COOLDOWN_DAYS = {"critical": 1, "warn": 3, "info": 7}
MAX_FINDINGS_POSTED = 25
SEV_RANK = {"critical": 0, "warn": 1, "info": 2}
SEV_EMOJI = {"critical": "\U0001f7e5", "warn": "\U0001f7e7", "info": "\U0001f7e6"}


def fetch_json(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def scan_all() -> tuple[list[dict], dict[str, dict], list[str]]:
    """Returns (all_findings, today_states_by_ticker, scan_errors). A
    ticker that fails outright contributes to scan_errors but is not
    silently dropped the way get_watchlist_snapshot() drops failures --
    see main()'s all-tickers-failed check below."""
    all_findings: list[dict] = []
    today_states: dict[str, dict] = {}
    scan_errors: list[str] = []

    for ticker in WATCHLIST:
        url = f"{BACKEND_URL}/api/internal/scan/{ticker}"
        if SECRET:
            url += f"?secret={SECRET}"
        try:
            result = fetch_json(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            scan_errors.append(f"{ticker}: request failed -- {exc}")
            continue

        for e in result.get("errors") or []:
            scan_errors.append(f"{ticker}: {e}")

        all_findings.extend(result.get("findings", []))
        today_states[ticker] = result.get("today_state", {})

    return all_findings, today_states, scan_errors


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def apply_cooldown(findings: list[dict], cooldowns: dict, today: datetime.date, force: bool) -> tuple[list[dict], dict]:
    """Every finding still gets recorded in the day's file regardless of
    cooldown (that's the full audit trail) -- cooldown only gates what
    actually gets pushed to Slack today."""
    to_post: list[dict] = []
    updated = dict(cooldowns)
    for f in findings:
        key = f["key"]
        window_days = COOLDOWN_DAYS.get(f["severity"], 3)
        last = updated.get(key)
        on_cooldown = False
        if last and not force:
            on_cooldown = (today - datetime.date.fromisoformat(last)).days < window_days
        if not on_cooldown:
            to_post.append(f)
            updated[key] = today.isoformat()
    return to_post, updated


def collect_weekly_info_roundup(today: datetime.date) -> list[dict]:
    """Info-severity findings from the prior 4 daily files that existed
    but weren't individually pushed (cooldown-suppressed) -- surfaced once
    on Fridays rather than never. Looks at PRIOR days only (today's own
    file hasn't been written yet at the point this is called)."""
    roundup: dict[str, dict] = {}
    for delta in range(1, 5):
        path = SCANS_DIR / f"{(today - datetime.timedelta(days=delta)).isoformat()}.json"
        record = load_json(path, None)
        if not record:
            continue
        posted_keys = set(record.get("posted", []))
        for f in record.get("findings", []):
            if f["severity"] == "info" and f["key"] not in posted_keys:
                roundup[f["key"]] = f  # last-seen wins
    return list(roundup.values())


def format_digest(to_post: list[dict], scan_errors: list[str], today: datetime.date) -> str:
    lines = [f"*Daily equity/options scan — {today.isoformat()}*"]

    if not to_post:
        lines.append("No new findings today (existing conditions still on cooldown, or genuinely quiet).")
    else:
        shown = to_post[:MAX_FINDINGS_POSTED]
        for f in shown:
            lines.append(f"{SEV_EMOJI.get(f['severity'], '')} *{f['title']}*\n{f['detail']}")
        if len(to_post) > MAX_FINDINGS_POSTED:
            lines.append(f"…and {len(to_post) - MAX_FINDINGS_POSTED} more.")

    if today.weekday() == 4:  # Friday
        roundup = collect_weekly_info_roundup(today)
        if roundup:
            lines.append("\n*This week's other observations (info-level, not individually pushed):*")
            for f in roundup[:15]:
                lines.append(f"{SEV_EMOJI['info']} {f['title']} — {f['detail']}")

    if scan_errors:
        shown_err = "; ".join(scan_errors[:5]) + ("..." if len(scan_errors) > 5 else "")
        lines.append(f"\n_Scan had {len(scan_errors)} sub-failure(s): {shown_err}_")

    return "\n\n".join(lines)


def post_slack(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        print("[no SLACK_WEBHOOK_URL set -- printing instead of posting]")
        print(text)
        return
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(SLACK_WEBHOOK_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"WARNING: Slack post failed: {exc}", file=sys.stderr)


def main() -> None:
    dry = "--dry" in sys.argv
    force = "--force" in sys.argv
    today = datetime.date.today()

    all_findings, today_states, scan_errors = scan_all()

    # Silence must be trustworthy. If every ticker failed, that's a broken
    # pipeline, not a quiet day -- an empty commit here would look
    # identical to "nothing happened," which is exactly how the ETH cloud
    # routine's staleness went unnoticed for weeks. Alert loudly instead.
    if not today_states:
        failure_text = (
            f"\U0001f6a8 *Daily scan FAILED* — 0/{len(WATCHLIST)} tickers scanned successfully.\n"
            + "\n".join(scan_errors[:10])
        )
        print(failure_text, file=sys.stderr)
        if not dry:
            post_slack(failure_text)
        sys.exit(1)

    cooldowns = load_json(COOLDOWN_PATH, {})
    to_post, updated_cooldowns = apply_cooldown(all_findings, cooldowns, today, force)
    to_post.sort(key=lambda f: SEV_RANK.get(f["severity"], 3))

    digest = format_digest(to_post, scan_errors, today)
    print(digest)
    print(f"\n[{len(today_states)}/{len(WATCHLIST)} tickers scanned, {len(all_findings)} total findings, {len(to_post)} posted, {len(scan_errors)} sub-failures]")

    if dry:
        print("\n[--dry: not writing files or posting to Slack]")
        return

    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    SCAN_STATE_DIR.mkdir(parents=True, exist_ok=True)

    day_record = {
        "date": today.isoformat(),
        "findings": all_findings,
        "posted": [f["key"] for f in to_post],
        "scan_errors": scan_errors,
        "tickers_scanned": sorted(today_states.keys()),
    }
    (SCANS_DIR / f"{today.isoformat()}.json").write_text(json.dumps(day_record, indent=2) + "\n")
    (SCANS_DIR / "latest.json").write_text(json.dumps(day_record, indent=2) + "\n")

    for ticker, state in today_states.items():
        (SCAN_STATE_DIR / f"{ticker}.json").write_text(json.dumps(state, indent=2) + "\n")

    COOLDOWN_PATH.write_text(json.dumps(updated_cooldowns, indent=2) + "\n")

    post_slack(digest)


if __name__ == "__main__":
    main()
