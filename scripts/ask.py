#!/usr/bin/env python3
"""Ad-hoc "analyze this stock" CLI -- the on-demand counterpart to the daily
scanner (scripts/scan_signals.py). Where the daily scan watches a fixed
watchlist and pushes alerts, this answers "what about SNOW, right now" for
whatever ticker someone names, and registers it in data/watchlist.json so
the daily scan picks it up going forward too -- so asking about a name once
means it's covered automatically after that. This is also the building
block the planned Slack Q&A bot (`?ask SNOW ...`) calls into: resolve
ticker -> fetch two-week options window -> build the classify-and-flag
prompt -> either hand it to local Ollama or hand it to the human to run
themselves.

Talks to the DEPLOYED backend by default (not a local one) so it works
without anything else running. Stdlib only, matching scan_signals.py /
snapshot_iv.py.

Usage:
  python scripts/ask.py SNOW                  # print link + prompt
  python scripts/ask.py SNOW --copy           # also copy prompt to clipboard (macOS pbcopy)
  python scripts/ask.py SNOW --run            # also run it against local Ollama and print the answer
  python scripts/ask.py SNOW --run --model llama3.2 --ollama-url http://localhost:11434
  python scripts/ask.py SNOW --horizon 21     # widen the options window beyond the default 2 weeks
  python scripts/ask.py SNOW --no-watchlist   # analyze without registering it for daily scans
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_URL = os.environ.get("BACKEND_URL", "https://backend-gules-iota-44.vercel.app")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://frontend-pi-blue-13.vercel.app")

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_ROOT / "data" / "watchlist.json"

DEFAULT_HORIZON_DAYS = 14
MAX_UNUSUAL_SHOWN = 8


def fetch_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "OSS-Terminal-ask/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"Error fetching {url}: HTTP {exc.code} -- {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Error fetching {url}: {exc}")


def resolve_ticker(ticker: str) -> dict:
    """Validates the ticker against the backend (same SEC-universe check
    every other endpoint uses) and returns its company profile, so an
    unresolvable name like NUGT fails loudly here rather than silently
    producing an empty prompt."""
    return fetch_json(f"{BACKEND_URL}/api/companies/{ticker}")


def load_watchlist() -> list[str]:
    if WATCHLIST_PATH.exists():
        try:
            data = json.loads(WATCHLIST_PATH.read_text())
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    return []


def add_to_watchlist(ticker: str) -> bool:
    """Returns True if the ticker was newly added. Writes the file locally
    only -- deliberately does NOT git add/commit/push, since that pushes to
    the shared public repo and this script can be run ad hoc many times a
    day. Print a reminder instead; committing is a decision to make
    explicitly, batched, not on every single lookup."""
    current = load_watchlist()
    if ticker in current:
        return False
    current = sorted(set(current) | {ticker})
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps(current, indent=2) + "\n")
    return True


def build_prompt(ticker: str, name: str, window: dict) -> str:
    ev = window["evidence"]
    exps = window["expirations"]
    unusual = window["unusual_contracts"]

    def fmt_evidence(items: list[dict]) -> str:
        if not items:
            return "  (none)"
        return "\n".join(f"  - {it['signal']}: {it['why']}" for it in items)

    exp_lines = []
    for p in exps:
        exp_lines.append(
            f"  - {p['expiration_date']} ({p['days_out']}d out): ATM IV "
            f"{p['atm_iv'] * 100:.0f}%" if p["atm_iv"] is not None else f"  - {p['expiration_date']} ({p['days_out']}d out): ATM IV n/a"
        )
        extra = (
            f", expected move ${p['expected_move']:.2f} ({p['expected_move_pct']:.1f}% of price)"
            if p.get("expected_move") is not None and p.get("expected_move_pct") is not None
            else ""
        )
        pc = f", P/C volume ratio {p['put_call_volume_ratio']:.2f}" if p.get("put_call_volume_ratio") is not None else ""
        exp_lines[-1] += f"{extra}{pc}, skew: {p['skew']}"

    unusual_lines = []
    for c in unusual[:MAX_UNUSUAL_SHOWN]:
        flag = " [expiring this week -- weeklies structurally run high volume:OI, discount unless extreme]" if c["expiring_this_week"] else " [NOT expiring this week -- genuinely new positioning]"
        ratio = f"{c['volume_oi_ratio']:.1f}x" if c.get("volume_oi_ratio") is not None else "n/a (new strike, ~0 prior OI)"
        unusual_lines.append(
            f"  - {c['contract_symbol']} ({c['side']}, strike ${c['strike']}, expires {c['expiration_date']}, "
            f"{c['days_out']}d out): volume {c['volume']:,} vs open interest {c['open_interest']:,} ({ratio}){flag}"
        )
    if not unusual_lines:
        unusual_lines = ["  (nothing crossed the volume/open-interest threshold in this window)"]

    tally = ev["tally"]
    lines = [
        f"You are a research assistant analyzing options market data for {name} ({ticker}) over the next "
        f"{window['horizon_days']} days for a retail investor.",
        "",
        "TASK 1 -- Classify the outlook into exactly three buckets: UP, DOWN, SIDEWAYS. Base this ONLY on the "
        "evidence listed below (do not invent evidence that isn't there). For each bucket, state how many "
        "evidence items support it and narrate briefly why. Some evidence may honestly support more than one "
        "bucket at once (e.g. a low RSI can mean both 'oversold bounce coming' and 'sustained selling') -- report "
        "that plainly rather than forcing a single winner. Do NOT declare which bucket will happen, and do NOT "
        "give buy/sell advice or recommend a specific options strategy -- describe only what the data shows.",
        "",
        "TASK 2 -- Call out any UNUSUAL ACTIVITY from the contract list below: which contracts have volume far "
        "exceeding open interest, and whether that's genuinely new positioning (not expiring this week) or just "
        "normal weekly open-interest reset (expiring this week, already flagged below -- discount those unless "
        "the size is extreme).",
        "",
        "RULES (do not break these, even if it feels natural to): no buy/sell advice, no options strategies "
        "(no \"buy calls\", \"long call\", \"bear put spread\", \"collar\", or similar). Do not mention or invent "
        "options Greeks (Delta, Gamma, Theta, Vega) or any bid/ask price -- none are provided below and you must "
        "not make them up. Use ONLY the numbers listed in DATA below; if something isn't listed, say it's not "
        "available rather than estimating or inventing it.",
        "",
        f"DATA (as of {window['as_of']}, underlying price ${window['underlying_price']}):",
        "",
        "Expirations in window:",
        *exp_lines,
        "",
        f"Evidence for UP ({tally['up']} item(s)):",
        fmt_evidence(ev["up"]),
        f"Evidence for DOWN ({tally['down']} item(s)):",
        fmt_evidence(ev["down"]),
        f"Evidence for SIDEWAYS ({tally['sideways']} item(s)):",
        fmt_evidence(ev["sideways"]),
        "",
        "Unusual contracts (top by volume):",
        *unusual_lines,
    ]
    if ev.get("event_week"):
        lines.append(f"\nNote: {ev['event_week']} carries notably richer IV than the rest of the window -- the market is pricing a dated event into that specific week.")
    lines.append(
        "\nReminder before you answer: no buy/sell advice, no options strategies, no invented Greeks or prices -- "
        "only the numbers listed above."
    )
    return "\n".join(lines)


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def run_ollama(base_url: str, model: str, prompt: str, timeout: int = 600) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False, "think": False,
        "options": {"num_predict": 700},
    }).encode()
    req = urllib.request.Request(f"{base_url}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except TimeoutError:
        # Observed live with qwen3.6 (36B) on this machine: a long prompt
        # can genuinely exceed 600s. urlopen's read-timeout surfaces as a
        # bare TimeoutError, not wrapped in URLError, on some Python
        # versions -- catch it explicitly rather than letting it crash
        # with a raw socket traceback.
        raise SystemExit(
            f"Ollama at {base_url} didn't respond within {timeout}s (model: {model}). "
            "Large local models can be this slow on a laptop -- try a smaller/faster model "
            "(e.g. `ollama pull llama3.2`) or rerun with a longer wait."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Couldn't reach Ollama at {base_url}: {exc}\n"
            "Is it running? (`ollama serve`, or check `ollama list` has a chat model pulled.)"
        )
    return data.get("response", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS, help=f"options window in days (default {DEFAULT_HORIZON_DAYS})")
    parser.add_argument("--copy", action="store_true", help="copy the prompt to clipboard (macOS)")
    parser.add_argument("--run", action="store_true", help="run the prompt against local Ollama and print the answer")
    parser.add_argument("--model", default="martain7r/finance-llama-8b:q4_k_m")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--no-watchlist", action="store_true", help="skip registering this ticker in data/watchlist.json")
    args = parser.parse_args()

    ticker = args.ticker.upper()

    profile = resolve_ticker(ticker)
    name = profile.get("name") or ticker
    print(f"Resolved: {ticker} -- {name}")

    window = fetch_json(f"{BACKEND_URL}/api/companies/{ticker}/options/two-week?horizon_days={args.horizon}")

    link = f"{FRONTEND_URL}/c/{ticker}/options"
    print(f"Link: {link}")

    if not args.no_watchlist:
        added = add_to_watchlist(ticker)
        if added:
            print(
                f"Added {ticker} to data/watchlist.json (locally). Not committed automatically -- "
                "`git add data/watchlist.json && git commit && git push` to have tomorrow's daily scan pick it up."
            )
        else:
            print(f"{ticker} already in data/watchlist.json -- covered by the daily scan.")

    prompt = build_prompt(ticker, name, window)

    tally = window["evidence"]["tally"]
    print(f"\nEvidence tally -- up: {tally['up']}, down: {tally['down']}, sideways: {tally['sideways']}")
    n_unusual = len(window["unusual_contracts"])
    print(f"Unusual contracts found: {n_unusual}")

    print("\n----- PROMPT -----\n")
    print(prompt)
    print("\n------------------\n")

    if args.copy:
        print("Copied to clipboard." if copy_to_clipboard(prompt) else "Clipboard copy not available on this platform.")

    if args.run:
        print(f"Running against local Ollama ({args.ollama_url}, model {args.model})...")
        answer = run_ollama(args.ollama_url, args.model, prompt)
        print("\n----- ANSWER -----\n")
        print(answer)
        print("\n-------------------\n")


if __name__ == "__main__":
    main()
