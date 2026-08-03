#!/usr/bin/env python3
"""Ad-hoc "analyze this stock" CLI -- the on-demand counterpart to the daily
scanner (scripts/scan_signals.py). Where the daily scan watches a fixed
watchlist and pushes alerts, this answers "what about SNOW, right now" for
whatever ticker someone names, and registers it in data/watchlist.json so
the daily scan picks it up going forward too -- so asking about a name once
means it's covered automatically after that. This is also the building
block the Slack Q&A bot (`?ask SNOW ...`) calls into: resolve ticker ->
fetch options data -> build a prompt -> either hand it to local Ollama or
hand it to the human to run themselves. Two prompts live here: the default
"classify into up/down/sideways and flag unusual activity" prompt
(build_prompt, two-week window), and a free-text strategy/outlook prompt
(build_strategy_prompt, --question below) that answers things like
base/bull/bear price ranges, expected move, IV rank, liquid strikes,
delta/theta/OI, probability of profit, scenario sensitivity, and event
risk for whatever horizon(s) the question names (or ~3/~6 months if it
names none).

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

  # Free-text strategy/outlook questions (base/bull/bear case, expected move, IV rank,
  # liquid strikes, delta/theta/OI, probability of profit, 5/10/15% sensitivity, event
  # risk, premium vs. spread) -- horizons are parsed from the question itself ("3 months",
  # "3- to 6-month", "Sept 18th 2026") and default to ~3 and ~6 months if none is named:
  python scripts/ask.py SPCX --question "What is the base-case, bull-case, and bear-case price outlook over the next 3 months and 6 months?"
  python scripts/ask.py SPCX --question "Which strikes have the best delta, theta, and open interest for directional trades?" --run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import options_math as optmath

BACKEND_URL = os.environ.get("BACKEND_URL", "https://backend-gules-iota-44.vercel.app")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://frontend-pi-blue-13.vercel.app")

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_ROOT / "data" / "watchlist.json"

DEFAULT_HORIZON_DAYS = 14
MAX_UNUSUAL_SHOWN = 8

# --- Strategy-question tuning (build_strategy_prompt and friends, below) ---
MAX_STRATEGY_HORIZON_DAYS = 200  # ~6.5 months -- cap how far any detected/defaulted horizon can widen a fetch
DEFAULT_STRATEGY_HORIZONS_DAYS = (91, 182)  # ~3 months, ~6 months -- used when a strategy question names no horizon of its own
LIQUID_STRIKES_PER_SIDE = 4  # how many strikes each side of spot to surface per expiration
MIN_STRIKE_LIQUIDITY = 10  # open interest OR volume floor below which a strike isn't worth calling "liquid"
SENSITIVITY_MOVES_PCT = (-15, -10, -5, 0, 5, 10, 15)


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


# --- Horizon detection --------------------------------------------------
# Shared by scripts/slack_bot.py so a free-text question like "...over the
# next 3 months and 6 months" or "...Sept 18th 2026" resolves to the same
# day counts everywhere it's parsed, rather than the CLI and the Slack bot
# quietly disagreeing about what "3 months" means.

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_DAY_RE = re.compile(
    # \s* (not \s+) between month and day -- real input observed live had
    # none at all: "Sept18th 2026" with no space before the day number.
    r"\b(" + "|".join(MONTH_NAMES) + r")\.?\s*(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")

# "3 months", "14 days", "3- to 6-month" (the literal phrasing this app's
# own example questions use), "3-6 months" -- the range alternation handles
# the "-? to " and "-" separators actually seen; anything fancier ("next
# quarter", "H2") isn't attempted, same philosophy as the date regexes
# above: a missed phrase falls through to a sensible default rather than
# risking a wrong number from a general-purpose parser.
_HORIZON_RANGE_RE = re.compile(
    r"\b(\d{1,2})(?:-?\s*to\s*|\s*-\s*)(\d{1,2})[- ]?(day|week|month)s?\b", re.IGNORECASE
)
_HORIZON_SINGLE_RE = re.compile(r"\b(\d{1,3})[- ]?(day|week|month)s?\b", re.IGNORECASE)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _literal_date_days_out(question: str, today: datetime.date) -> int | None:
    """Finds an explicit calendar date in `question` (month name or
    numeric) and returns how many days out it is, with a +7 day buffer
    since the mentioned date isn't necessarily itself a real expiration
    (options expire on specific weekdays) -- widen slightly so the nearest
    real expiration on/after it is still included. Returns None if no
    literal date is found; a bare "3 months" phrase is handled separately
    by parse_horizons_days, not here."""
    target: datetime.date | None = None

    m = _MONTH_DAY_RE.search(question)
    if m:
        month = MONTH_NAMES[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            candidate = datetime.date(year, month, day)
        except ValueError:
            candidate = None
        if candidate and not m.group(3) and candidate < today:
            candidate = datetime.date(year + 1, month, day)
        target = candidate
    else:
        m = _NUMERIC_DATE_RE.search(question)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            year_str = m.group(3)
            year = today.year if not year_str else (2000 + int(year_str) if len(year_str) == 2 else int(year_str))
            try:
                candidate = datetime.date(year, month, day)
            except ValueError:
                candidate = None
            if candidate and not year_str and candidate < today:
                candidate = datetime.date(year + 1, month, day)
            target = candidate

    if target is None:
        return None
    return (target - today).days + 7


def parse_horizons_days(question: str, today: datetime.date | None = None) -> list[int]:
    """Scans free text for every horizon it names -- a literal date, a
    single "N month/week/day" phrase, or a range like "3- to 6-month" --
    and returns the distinct day counts, sorted ascending. Returns an empty
    list if nothing was found; callers decide the fallback (see
    DEFAULT_STRATEGY_HORIZONS_DAYS)."""
    if not question:
        return []
    today = today or datetime.date.today()
    found: set[int] = set()

    literal = _literal_date_days_out(question, today)
    if literal is not None and 1 <= literal <= MAX_STRATEGY_HORIZON_DAYS:
        found.add(literal)

    for m in _HORIZON_RANGE_RE.finditer(question):
        unit_days = _UNIT_DAYS[m.group(3).lower()]
        for n in (int(m.group(1)), int(m.group(2))):
            d = n * unit_days
            if 1 <= d <= MAX_STRATEGY_HORIZON_DAYS:
                found.add(d)

    # Strip range matches before scanning for singles so "3- to 6-month"
    # doesn't also register a spurious single "6 month" match.
    remainder = _HORIZON_RANGE_RE.sub(" ", question)
    for m in _HORIZON_SINGLE_RE.finditer(remainder):
        d = int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()]
        if 1 <= d <= MAX_STRATEGY_HORIZON_DAYS:
            found.add(d)

    return sorted(found)


# --- Strategy-question data helpers --------------------------------------
# Everything below computes real numbers from the chain data already being
# fetched (strike, underlying price, IV, days to expiry) rather than asking
# the model to estimate them. Yahoo's chain has no Greeks field at all, so
# this is the only way to answer a delta/theta/probability-of-profit
# question without inventing one -- same "compute in Python, narrate in the
# prompt" split the two-week evidence tally above already uses.

def fetch_json_optional(url: str, timeout: int = 30) -> dict | None:
    """Like fetch_json, but returns None instead of raising -- for data
    that makes an answer better when present (IV rank, recent filings) but
    shouldn't block the whole prompt if that one upstream call fails."""
    try:
        return fetch_json(url, timeout=timeout)
    except SystemExit:
        return None


def nearest_expiration(expiration_dates: list[int], target_days: int) -> int | None:
    """Pick the listed expiration closest to `target_days` out -- same
    convention as the backend's closest_expiration_to_days, run client-side
    here since the caller already has the expiration list in hand."""
    if not expiration_dates:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    target_seconds = target_days * 86400
    return min(expiration_dates, key=lambda exp: abs((exp - now) - target_seconds))


def _days_out(expiration: int) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return max(0, round((expiration - now) / 86400))


def mid_price(contract: dict) -> float | None:
    bid, ask = contract.get("bid"), contract.get("ask")
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return contract.get("last_price") or None


def pick_liquid_strikes(contracts: list[dict], center: float, count: int, min_liquidity: int = MIN_STRIKE_LIQUIDITY) -> list[dict]:
    """Strikes closest to `center`, preferring ones that actually trade
    (open interest or volume at/above `min_liquidity`) so "liquid strikes"
    means something real rather than just "nearest strike, dead or not."
    Falls back to the nearest strikes regardless of liquidity if nothing
    clears the bar -- a thin name should show low OI/volume honestly
    rather than vanish from the answer."""
    if not contracts:
        return []
    liquid = [c for c in contracts if (c.get("open_interest") or 0) >= min_liquidity or (c.get("volume") or 0) >= min_liquidity]
    pool = liquid or contracts
    return sorted(pool, key=lambda c: abs(c["strike"] - center))[:count]


def _payoff_at_expiry(structure: dict, spot_at_expiry: float) -> float | None:
    """Intrinsic-value-at-expiration payoff, per share, net of the
    structure's own cost -- the "if the stock is flat / up / down by
    expiration" figure, not a pre-expiration mark-to-market (which would
    also depend on remaining theta/vega, described qualitatively via the
    Greeks already attached to each structure instead)."""
    t = structure["type"]
    if t == "long_call":
        return max(spot_at_expiry - structure["strike"], 0.0) - structure["premium"]
    if t == "long_put":
        return max(structure["strike"] - spot_at_expiry, 0.0) - structure["premium"]
    if t == "call_debit_spread":
        long_val = max(spot_at_expiry - structure["long_strike"], 0.0)
        short_val = max(spot_at_expiry - structure["short_strike"], 0.0)
        return (long_val - short_val) - structure["net_debit"]
    if t == "put_debit_spread":
        long_val = max(structure["long_strike"] - spot_at_expiry, 0.0)
        short_val = max(structure["short_strike"] - spot_at_expiry, 0.0)
        return (long_val - short_val) - structure["net_debit"]
    return None


def sensitivity_table(structures: dict[str, dict | None], spot: float) -> list[dict]:
    rows = []
    for pct in SENSITIVITY_MOVES_PCT:
        spot_at_expiry = spot * (1 + pct / 100)
        row: dict = {"move_pct": pct, "price": spot_at_expiry}
        for key, structure in structures.items():
            row[key] = _payoff_at_expiry(structure, spot_at_expiry) if structure else None
        rows.append(row)
    return rows


def build_horizon_analysis(ticker: str, spot: float, expiration: int, target_days: int) -> dict | None:
    """Fetches the full chain for one expiration and computes everything a
    strategy question needs about it: ATM IV, the options-implied 1-SD
    expected move, liquid strikes near the money with Black-Scholes
    Greeks, and two representative debit spreads (call and put) built from
    those same strikes, plus an at-expiration sensitivity table for all
    four structures. Returns None only if the chain itself can't be
    fetched (e.g. an expiration with no listed contracts)."""
    try:
        chain = fetch_json(f"{BACKEND_URL}/api/companies/{ticker}/options?expiration={expiration}")
    except SystemExit:
        return None

    days_out = _days_out(expiration)
    s = chain["summary"]
    ivs = [v for v in (s.get("atm_call_iv"), s.get("atm_put_iv")) if v]
    atm_iv = (sum(ivs) / len(ivs)) if ivs else None
    move_1sd = optmath.expected_move(spot, atm_iv, days_out) if atm_iv else None

    def enrich(contracts: list[dict], option_type: str) -> list[dict]:
        out = []
        for c in contracts:
            iv = c.get("implied_volatility") or atm_iv
            g = optmath.greeks(spot, c["strike"], days_out, iv, option_type) if iv else None
            out.append({**c, "greeks": g})
        return out

    def enrich_one(contracts: list[dict], target: float | None, option_type: str) -> dict | None:
        """The single liquid contract closest to `target`, searched across
        the FULL chain for this side -- not the near-the-money display list
        below, which is deliberately narrow (LIQUID_STRIKES_PER_SIDE) and
        would silently exclude a real ~1-SD-out strike on a wide chain,
        collapsing the OTM leg of every spread onto the ATM one."""
        if target is None:
            return None
        picked = pick_liquid_strikes(contracts, target, 1)
        return enrich(picked, option_type)[0] if picked else None

    # Liquid strikes near the money -- what the answer shows as "the most
    # liquid strikes," a deliberately short list for readability.
    liquid_calls = enrich(pick_liquid_strikes(chain["calls"], spot, LIQUID_STRIKES_PER_SIDE), "call")
    liquid_puts = enrich(pick_liquid_strikes(chain["puts"], spot, LIQUID_STRIKES_PER_SIDE), "put")

    # The FULL chain, every strike, both sides -- not just the near-the-money
    # subset above. Same chain fetch already in hand, no extra network call.
    all_calls = sorted(enrich(chain["calls"], "call"), key=lambda c: c["strike"])
    all_puts = sorted(enrich(chain["puts"], "put"), key=lambda c: c["strike"])

    # ATM and ~1-SD-out strikes for the representative structures below --
    # searched across the full chain (see enrich_one) so the OTM leg is a
    # real ~1-SD strike, not whatever happened to land in the near-ATM list.
    atm_call = enrich_one(chain["calls"], spot, "call")
    atm_put = enrich_one(chain["puts"], spot, "put")
    otm_call = enrich_one(chain["calls"], spot + move_1sd, "call") if move_1sd else None
    otm_put = enrich_one(chain["puts"], spot - move_1sd, "put") if move_1sd else None

    def long_structure(contract: dict | None, option_type: str) -> dict | None:
        if not contract:
            return None
        premium = mid_price(contract)
        iv = contract.get("implied_volatility") or atm_iv
        if premium is None or not iv:
            return None
        breakeven = contract["strike"] + premium if option_type == "call" else contract["strike"] - premium
        pop = (optmath.prob_above if option_type == "call" else optmath.prob_below)(spot, breakeven, days_out, iv)
        g = contract.get("greeks") or {}
        return {
            "type": f"long_{option_type}", "strike": contract["strike"], "premium": premium,
            "breakeven": breakeven, "open_interest": contract.get("open_interest"), "volume": contract.get("volume"),
            "delta": g.get("delta"), "theta_per_day": g.get("theta_per_day"), "vega": g.get("vega"),
            "prob_of_profit": pop,
        }

    def spread_structure(long_c: dict | None, short_c: dict | None, option_type: str) -> dict | None:
        if not long_c or not short_c or long_c["strike"] == short_c["strike"]:
            return None
        long_prem, short_prem = mid_price(long_c), mid_price(short_c)
        if long_prem is None or short_prem is None:
            return None
        net_debit = long_prem - short_prem
        if net_debit <= 0:
            return None  # a genuine debit spread only -- a credit here means the strikes picked aren't a clean debit structure
        width = abs(short_c["strike"] - long_c["strike"])
        if option_type == "call":
            structure_type, breakeven = "call_debit_spread", long_c["strike"] + net_debit
            pop = optmath.prob_above(spot, breakeven, days_out, atm_iv) if atm_iv else None
        else:
            structure_type, breakeven = "put_debit_spread", long_c["strike"] - net_debit
            pop = optmath.prob_below(spot, breakeven, days_out, atm_iv) if atm_iv else None
        return {
            "type": structure_type, "long_strike": long_c["strike"], "short_strike": short_c["strike"],
            "net_debit": net_debit, "max_profit": width - net_debit, "max_loss": net_debit,
            "breakeven": breakeven, "prob_of_profit": pop,
        }

    structures = {
        "long_call": long_structure(atm_call, "call"),
        "long_put": long_structure(atm_put, "put"),
        "call_debit_spread": spread_structure(atm_call, otm_call, "call"),
        "put_debit_spread": spread_structure(atm_put, otm_put, "put"),
    }

    return {
        "expiration": expiration,
        "expiration_date": datetime.date.fromtimestamp(expiration).isoformat(),
        "days_out": days_out,
        "target_days": target_days,
        "atm_iv": atm_iv,
        "expected_move_1sd": move_1sd,
        # A fraction (0.045 = 4.5%), not a pre-multiplied percentage -- _fmt_pct below does the *100.
        "expected_move_1sd_pct": (move_1sd / spot) if move_1sd and spot else None,
        "liquid_calls": liquid_calls,
        "liquid_puts": liquid_puts,
        "all_calls": all_calls,
        "all_puts": all_puts,
        "structures": structures,
        "sensitivity": sensitivity_table(structures, spot),
    }


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


def _fmt_pct(x: float | None, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%" if x is not None else "n/a"


def _fmt_usd(x: float | None, digits: int = 2) -> str:
    return f"${x:,.{digits}f}" if x is not None else "n/a"


def _fmt_strike_row(c: dict) -> str:
    g = c.get("greeks") or {}
    iv = _fmt_pct(c.get("implied_volatility"), 0)
    delta = f"{g['delta']:.2f}" if g.get("delta") is not None else "n/a"
    # 3 decimals, not 2 -- daily theta on a several-months-out contract is
    # routinely a few tenths of a cent and rounds to an uninformative
    # "$-0.01" at 2 decimals.
    theta = _fmt_usd(g.get("theta_per_day"), digits=3) if g.get("theta_per_day") is not None else "n/a"
    return (
        f"    - ${c['strike']}: OI {c.get('open_interest', 0):,}, volume {c.get('volume', 0):,}, "
        f"IV {iv}, delta {delta}, theta {theta}/day"
    )


def _fmt_structures(structures: dict[str, dict | None]) -> list[str]:
    lines = []
    lc, lp = structures.get("long_call"), structures.get("long_put")
    cs, ps = structures.get("call_debit_spread"), structures.get("put_debit_spread")
    if lc:
        lines.append(
            f"    - Long call ${lc['strike']}: premium {_fmt_usd(lc['premium'])}, breakeven {_fmt_usd(lc['breakeven'])}, "
            f"delta {lc['delta']:.2f}, theta {_fmt_usd(lc['theta_per_day'], digits=3)}/day, vega {_fmt_usd(lc['vega'], digits=3)} "
            f"(per 1pt IV), OI {lc.get('open_interest', 0):,}, prob. of finishing above breakeven {_fmt_pct(lc['prob_of_profit'])}"
        )
    if lp:
        lines.append(
            f"    - Long put ${lp['strike']}: premium {_fmt_usd(lp['premium'])}, breakeven {_fmt_usd(lp['breakeven'])}, "
            f"delta {lp['delta']:.2f}, theta {_fmt_usd(lp['theta_per_day'], digits=3)}/day, vega {_fmt_usd(lp['vega'], digits=3)} "
            f"(per 1pt IV), OI {lp.get('open_interest', 0):,}, prob. of finishing below breakeven {_fmt_pct(lp['prob_of_profit'])}"
        )
    if cs:
        lines.append(
            f"    - Call debit spread (long ${cs['long_strike']} / short ${cs['short_strike']}): net debit {_fmt_usd(cs['net_debit'])}, "
            f"max profit {_fmt_usd(cs['max_profit'])}, max loss {_fmt_usd(cs['max_loss'])}, breakeven {_fmt_usd(cs['breakeven'])}, "
            f"prob. of profit {_fmt_pct(cs['prob_of_profit'])}"
        )
    if ps:
        lines.append(
            f"    - Put debit spread (long ${ps['long_strike']} / short ${ps['short_strike']}): net debit {_fmt_usd(ps['net_debit'])}, "
            f"max profit {_fmt_usd(ps['max_profit'])}, max loss {_fmt_usd(ps['max_loss'])}, breakeven {_fmt_usd(ps['breakeven'])}, "
            f"prob. of profit {_fmt_pct(ps['prob_of_profit'])}"
        )
    if not lines:
        lines.append("    (not enough liquid/priced strikes near the money to build a structure at this expiration)")
    return lines


def _fmt_sensitivity(rows: list[dict]) -> list[str]:
    header = f"    {'move':>6} {'price':>10} {'long call':>10} {'long put':>10} {'call spread':>12} {'put spread':>11}"
    lines = [header]
    for row in rows:
        def cell(key: str) -> str:
            v = row.get(key)
            return _fmt_usd(v) if v is not None else "n/a"
        lines.append(
            f"    {row['move_pct']:>+5}% {_fmt_usd(row['price']):>10} {cell('long_call'):>10} {cell('long_put'):>10} "
            f"{cell('call_debit_spread'):>12} {cell('put_debit_spread'):>11}"
        )
    return lines


def build_strategy_prompt(ticker: str, name: str, question: str, horizons_days: list[int] | None = None) -> tuple[str, dict]:
    """The prompt behind free-text strategy/outlook questions -- "base/bull/
    bear case", "what does the options chain imply", "IV rank", "most
    liquid strikes", "delta/theta/OI", "probability of profit", "5/10/15%
    sensitivity", "event risk", "premium vs. spread" -- for ANY ticker, not
    just the fixed watchlist scan_signals.py tracks.

    Same split as build_prompt above: every number in DATA is computed
    here in Python (Black-Scholes Greeks/POP from options_math, the
    options-implied expected move, the liquidity filter, the sensitivity
    table) -- the model's job is narration and synthesis, not arithmetic,
    for the same reason the two-week evidence tally keeps that split: an
    LLM handed raw chain data will happily invent numbers that sound
    plausible instead of using the real ones.
    """
    explicit_horizons = horizons_days or parse_horizons_days(question)
    horizons_assumed = not explicit_horizons
    horizons = sorted(set(explicit_horizons or DEFAULT_STRATEGY_HORIZONS_DAYS))[:3]

    base_chain = fetch_json(f"{BACKEND_URL}/api/companies/{ticker}/options")
    spot = base_chain.get("underlying_price")
    expiration_dates = base_chain.get("expiration_dates", [])

    seen_expirations: dict[int, int] = {}  # expiration -> the target_days that first picked it
    for d in horizons:
        exp = nearest_expiration(expiration_dates, d)
        if exp is not None and exp not in seen_expirations:
            seen_expirations[exp] = d

    analyses = []
    for exp, target_days in seen_expirations.items():
        analysis = build_horizon_analysis(ticker, spot, exp, target_days) if spot else None
        if analysis:
            analyses.append(analysis)
    analyses.sort(key=lambda a: a["days_out"])

    near_window = fetch_json_optional(f"{BACKEND_URL}/api/companies/{ticker}/options/two-week?horizon_days=14")
    iv_rank = fetch_json_optional(f"{BACKEND_URL}/api/companies/{ticker}/options/iv-rank")
    filings = fetch_json_optional(f"{BACKEND_URL}/api/companies/{ticker}/filings?limit=8")

    lines = [
        f"You are a research assistant helping a retail investor reason through options strategy questions for "
        f"{name} ({ticker}). Answer the QUESTION at the very end of this prompt, using ONLY the DATA below plus "
        "ordinary options-pricing reasoning -- do not invent any figure (price, IV, Greek, OI, volume, or "
        "probability) that isn't either listed in DATA or a direct arithmetic consequence of numbers that are.",
        "",
        "RULES (do not break these, even if it feels natural to):",
        "  - Every Greek (delta, theta, vega) and every probability-of-profit figure in DATA is a Black-Scholes "
        "theoretical estimate computed from the contract's own quoted IV -- NOT a broker-quoted number. Say so if "
        "you state one, and don't present it as more precise than that (real dividends, borrow cost, and vol-model "
        "differences mean a broker's own numbers will differ slightly).",
        "  - Probability-of-profit figures are RISK-NEUTRAL probabilities (the standard convention, same as most "
        "retail platforms), not a guaranteed real-world forecast.",
        "  - You may describe how specific structures (long calls/puts, debit spreads, calendars) behave and "
        "compare them factually -- that is what was asked. Do NOT tell the user which one to actually execute, "
        "recommend a position size, or state a conviction that a specific outcome WILL happen. Frame bull/base/bear "
        "cases as 'if [evidence] plays out' scenarios, not predictions.",
        "  - If the question needs a specific date, strike, target price, or expiration that isn't stated and "
        "isn't reasonably inferable from DATA below, do not guess at it -- ask a short, specific clarifying "
        "question about exactly what's missing instead of answering as if you knew.",
        "  - This tool has no earnings calendar -- if the question needs a confirmed earnings/event date, say that "
        "plainly and point to the recent filings list (or the company's own IR page) rather than inventing a date.",
        "  - Not investment advice; this is descriptive market data, not a recommendation.",
        "",
        f"DATA (as of {datetime.date.today().isoformat()}, {ticker} spot price {_fmt_usd(spot)}):",
    ]

    if horizons_assumed:
        lines.append(
            f"  (No specific timeframe was named in the question, so this defaults to the two horizons most often "
            f"asked about here, ~{horizons[0]} and ~{horizons[-1]} days out. Say so in your answer, and invite the "
            "user to name a different date/timeframe if that's not what they meant.)"
        )
    lines.append("")

    if iv_rank:
        if iv_rank.get("iv_rank") is not None:
            lines.append(
                f"IV RANK/PERCENTILE: current ATM IV ~{_fmt_pct(iv_rank['current_iv'], 0)}, IV rank "
                f"{iv_rank['iv_rank']:.0f}/100, IV percentile {iv_rank['iv_percentile']:.0f}% "
                f"({iv_rank['note']})"
            )
        else:
            lines.append(f"IV RANK/PERCENTILE: not available -- {iv_rank.get('note', 'insufficient history collected for this ticker')}.")
    else:
        lines.append("IV RANK/PERCENTILE: not available (couldn't fetch IV history for this ticker).")
    lines.append("")

    for a in analyses:
        lines.append(
            f"EXPIRATION {a['expiration_date']} (~{a['target_days']}d horizon requested, {a['days_out']}d actual out, "
            f"ATM IV {_fmt_pct(a['atm_iv'], 0)}):"
        )
        if a["expected_move_1sd"] is not None and spot:
            lines.append(
                f"  Options-implied 1-SD expected move by this expiration: {_fmt_usd(a['expected_move_1sd'])} "
                f"({_fmt_pct(a['expected_move_1sd_pct'])} of spot) -> bull-case reference level "
                f"{_fmt_usd(spot + a['expected_move_1sd'])}, base-case (spot) {_fmt_usd(spot)}, bear-case reference "
                f"level {_fmt_usd(spot - a['expected_move_1sd'])}. (~68% of a lognormal distribution falls within "
                "1 SD -- these are option-market-implied reference levels, not a prediction of where price will land.)"
            )
        else:
            lines.append("  Options-implied expected move: not available (no usable ATM IV at this expiration).")
        lines.append("  Liquid calls near the money:")
        lines.extend(_fmt_strike_row(c) for c in a["liquid_calls"]) if a["liquid_calls"] else lines.append("    (none met the liquidity floor)")
        lines.append("  Liquid puts near the money:")
        lines.extend(_fmt_strike_row(c) for c in a["liquid_puts"]) if a["liquid_puts"] else lines.append("    (none met the liquidity floor)")
        lines.append("  Representative structures at this expiration (Black-Scholes-derived, see RULES):")
        lines.extend(_fmt_structures(a["structures"]))
        lines.append(f"  Sensitivity to the underlying's price by this expiration (payoff per share, net of cost/credit):")
        lines.extend(_fmt_sensitivity(a["sensitivity"]))
        lines.append(
            f"  FULL OPTIONS CHAIN at this expiration -- every listed strike, not just the near-the-money ones "
            f"above (use this for any question about a specific strike, the full distribution of OI/volume, or "
            f"strikes further from the money than what's highlighted elsewhere):"
        )
        lines.append(f"  All calls ({len(a['all_calls'])} strikes):")
        lines.extend(_fmt_strike_row(c) for c in a["all_calls"]) if a["all_calls"] else lines.append("    (none listed)")
        lines.append(f"  All puts ({len(a['all_puts'])} strikes):")
        lines.extend(_fmt_strike_row(c) for c in a["all_puts"]) if a["all_puts"] else lines.append("    (none listed)")
        lines.append("")

    if len(analyses) >= 2:
        front, back = analyses[0], analyses[-1]
        front_theta = ((front["structures"].get("long_call") or {}).get("theta_per_day"))
        back_theta = ((back["structures"].get("long_call") or {}).get("theta_per_day"))
        lines.append(
            "CALENDAR SPREADS (same strike, different expirations): not modeled with a precise probability-of-"
            "profit above -- that requires jointly modeling both expirations' distributions, which isn't attempted "
            "here. What IS in DATA that's directly relevant: the near-dated ATM call's theta "
            f"({_fmt_usd(front_theta, digits=3)}/day at {front['expiration_date']}) vs the further-dated ATM call's theta "
            f"({_fmt_usd(back_theta, digits=3)}/day at {back['expiration_date']}) -- a calendar is built short the faster-"
            "decaying near leg and long the slower-decaying far leg, and tends to profit most if price pins near "
            "the shared strike through the near expiration. Also compare ATM IV across the two expirations above "
            "(term structure) -- a calendar benefits from the front IV falling faster than the back IV as the near "
            "expiration approaches."
        )
        lines.append("")

    if near_window:
        ev = near_window["evidence"]
        tally = ev["tally"]

        def fmt_ev(items: list[dict]) -> str:
            return "; ".join(f"{it['signal']}" for it in items) if items else "none"

        if not horizons_assumed:
            lines.append(
                f"(The section below always covers the next 14 days regardless of what was asked -- it is "
                f"supplementary color on the CURRENT lean, not the answer to the requested "
                f"{', '.join(f'~{d}d' for d in horizons)}-day horizon(s). The EXPIRATION section(s) above, dated "
                f"{', '.join(a['expiration_date'] for a in analyses)}, are what actually answer the question.)"
            )
        lines.append(
            "NEAR-TERM (14-DAY) DIRECTIONAL EVIDENCE (context only -- computed over the next two weeks, not the "
            "horizon(s) above; use this to describe the CURRENT lean, not as a forecast for the requested horizon):"
        )
        lines.append(f"  Evidence for UP ({tally['up']}): {fmt_ev(ev['up'])}")
        lines.append(f"  Evidence for DOWN ({tally['down']}): {fmt_ev(ev['down'])}")
        lines.append(f"  Evidence for SIDEWAYS ({tally['sideways']}): {fmt_ev(ev['sideways'])}")
        if ev.get("event_week"):
            lines.append(f"  Event pricing flag: {ev['event_week']} carries notably richer IV than its neighbors -- the market is pricing a dated event into that week.")
        lines.append("")
    else:
        lines.append("NEAR-TERM DIRECTIONAL EVIDENCE: not available (couldn't fetch the two-week window).")
        lines.append("")

    if filings and filings.get("filings"):
        lines.append(
            "RECENT SEC FILINGS (event-risk context, most recent first -- NOT an earnings calendar; this tool "
            "doesn't have one, so if the question needs a confirmed earnings date, say that explicitly):"
        )
        for f in filings["filings"]:
            lines.append(f"  - {f['form']} filed {f['filing_date']} (report period {f.get('report_date') or 'n/a'}): {f['filing_index_url']}")
        lines.append("")
    else:
        lines.append("RECENT SEC FILINGS: not available for this ticker (may not be an SEC operating-company filer, e.g. an ETF).")
        lines.append("")

    lines.append(f"QUESTION: {question}")
    lines.append(
        "\nAnswer the question directly, organized by whichever of these it actually touches (skip sections it "
        "doesn't ask about): base/bull/bear price outlook per horizon, what the options chain implies about "
        "expected move, IV rank read (elevated/cheap/unavailable), the most liquid expirations/strikes, "
        "best delta/theta/OI picks for directional exposure, probability of profit for each structure, "
        "sensitivity to 5/10/15% moves, flat/up/down-by-expiration outcomes, event risk, and whether the expected "
        "move justifies buying premium vs. a spread (compare the structures' theta decay and breakeven distance "
        "from the expected-move reference levels above -- factually, not as a recommendation)."
    )

    return "\n".join(lines), {
        "name": name, "spot": spot, "horizons_days": horizons, "horizons_assumed": horizons_assumed,
        "expirations": [a["expiration_date"] for a in analyses],
    }


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def run_ollama(base_url: str, model: str, prompt: str, timeout: int = 600) -> str:
    # Explicit num_ctx, not left to the model's Modelfile default -- observed
    # live that finance-llama-8b's Modelfile pins num_ctx to 4096 despite the
    # underlying model supporting 131072. A multi-expiration strategy prompt
    # (especially with the full options chain included, see build_strategy_
    # prompt) routinely runs well past 4096 tokens; Ollama silently drops the
    # overflow from the FRONT of the prompt to make room, which was quietly
    # eating the RULES block and the nearest-dated EXPIRATION section while
    # leaving the always-present NEAR-TERM (14-DAY) evidence intact near the
    # tail -- so a question about a date months out would come back reasoned
    # almost entirely off the 2-week evidence instead. 32768 covers even a
    # full-chain, 3-expiration prompt with headroom.
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False, "think": False,
        "options": {"num_predict": 700, "num_ctx": 32768},
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
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS, help=f"options window in days (default {DEFAULT_HORIZON_DAYS}) -- ignored if --question is given")
    parser.add_argument("--question", default=None, help="ask a free-text strategy/outlook question instead of the default classify-and-flag prompt (e.g. \"base/bull/bear case over the next 3 and 6 months\")")
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

    if args.question:
        prompt, meta = build_strategy_prompt(ticker, name, args.question)
        horizons_note = "assumed (none named in the question)" if meta["horizons_assumed"] else "detected in the question"
        print(f"\nHorizons used ({horizons_note}): {meta['horizons_days']} days out -> expirations {meta['expirations']}")
    else:
        window = fetch_json(f"{BACKEND_URL}/api/companies/{ticker}/options/two-week?horizon_days={args.horizon}")
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
