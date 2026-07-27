#!/usr/bin/env python3
"""Local Slack Q&A daemon for #equity-alerts -- polls the channel for
`?ask TICKER [optional question]`, answers using local Ollama, posts the
reply back in a thread. Runs entirely on this laptop; nobody's Claude
account is involved, and nothing is exposed to the public internet -- it's
an outbound-polling client, not a server.

Reuses every piece of scripts/ask.py (ticker resolution, the two-week
options window, the classify-and-flag-unusual-activity prompt, the Ollama
call) rather than reimplementing any of it, so the Slack answer and the
terminal `python scripts/ask.py TICKER` answer are always the same logic.

Config via environment variables (or a `.env` file next to this script --
see scripts/.env.example). Required:
  SLACK_BOT_TOKEN   Bot User OAuth Token (starts xoxb-), scopes needed:
                     channels:history, channels:read, chat:write
                     (add groups:history/groups:read too if the channel is
                     private) -- and the bot must be INVITED to the channel.
  SLACK_CHANNEL     channel ID (C...) or name (#equity-alerts / equity-alerts)

Optional (defaults shown):
  OLLAMA_URL=http://localhost:11434
  OLLAMA_MODEL=llama3.2
  POLL_INTERVAL_SECONDS=20
  COMMAND_PREFIX=?ask
  BACKEND_URL / FRONTEND_URL -- same defaults as ask.py

Usage:
  python scripts/slack_bot.py            # run forever
  python scripts/slack_bot.py --once     # poll once and exit (testing)
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ask as ask_lib  # noqa: E402  (reuse resolve_ticker/build_prompt/run_ollama/etc.)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "scripts" / ".slackbot_state.json"
ENV_PATH = Path(__file__).resolve().parent / ".env"

SLACK_API = "https://slack.com/api"
MAX_REPLY_CHARS = 3800  # comfortably under Slack's message limit; Ollama's num_predict cap keeps real answers well under this anyway


def load_dotenv(path: Path) -> None:
    """Minimal .env loader -- no dependency, and needed because a launchd
    daemon doesn't inherit your shell's exported variables. Real
    environment variables always win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(ENV_PATH)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
COMMAND_PREFIX = os.environ.get("COMMAND_PREFIX", "?ask").lower()


def slack_call(method: str, params: dict, http_method: str = "POST") -> dict:
    if not SLACK_BOT_TOKEN:
        raise SystemExit("SLACK_BOT_TOKEN not set -- see scripts/.env.example")
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    if http_method == "POST":
        body = json.dumps(params).encode()
        headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(f"{SLACK_API}/{method}", data=body, headers=headers)
    else:
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{SLACK_API}/{method}?{query}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {data.get('error')}")
    return data


def resolve_channel_id(channel: str) -> str:
    channel = channel.strip()
    if channel.startswith("C") and channel.isupper():
        return channel
    target_name = channel.lstrip("#").lower()
    cursor = None
    while True:
        params = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = slack_call("conversations.list", params, http_method="GET")
        for ch in data.get("channels", []):
            if ch.get("name", "").lower() == target_name:
                return ch["id"]
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    raise SystemExit(f"Couldn't find a channel named '{channel}' -- is the bot invited to it?")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def post_message(channel_id: str, text: str, thread_ts: str | None = None) -> str:
    params = {"channel": channel_id, "text": text}
    if thread_ts:
        params["thread_ts"] = thread_ts
    data = slack_call("chat.postMessage", params)
    return data["ts"]


def parse_command(text: str) -> tuple[str, str] | None:
    """Returns (ticker, extra_question) if `text` matches `[?]COMMAND_PREFIX
    TICKER [rest...]`, else None. Ticker must look like a real ticker (1-6
    letters, optional -A/-B class suffix) so "?ask what's happening" (no
    ticker) doesn't get misparsed.

    The leading "?" in COMMAND_PREFIX (default "?ask") is treated as
    optional -- observed live that a real Slack client message came through
    as "ask SNOW ..." with the "?" simply not present (client-side
    autocorrect or similar), so requiring it verbatim would silently drop
    real questions. Matching is on the core word, punctuation is cosmetic.
    """
    core_prefix = COMMAND_PREFIX.lstrip("?")
    stripped = text.strip().lstrip("?").strip()
    if not stripped.lower().startswith(core_prefix):
        return None
    rest = stripped[len(core_prefix):].strip()
    if not rest:
        return None
    parts = rest.split(None, 1)
    candidate = parts[0].upper().strip(".,!?")
    ticker_body = candidate.split("-")[0]
    if not (1 <= len(ticker_body) <= 6 and ticker_body.isalpha()):
        return None
    extra = parts[1].strip() if len(parts) > 1 else ""
    return candidate, extra


def parse_reply_question(text: str) -> str | None:
    """Returns the follow-up question for a reply inside a thread that
    already has an established ticker (see poll_once) -- or None only for
    an empty/whitespace message.

    Deliberately does NOT require the "ask"/"?ask" prefix here, unlike
    parse_command. Observed live, twice: (1) "?ask does it carry more
    weight on the call side or put side with 140% IV on spcx" has the
    ticker at the END of the sentence, not the first word, so ticker-
    shaped-first-word parsing fails; (2) a later reply in the same
    conversation, "?any unusual option activity for the next 3 weeks",
    doesn't even start with "ask" and got silently ignored under the old
    rule. Once someone is already talking to the bot in a thread it
    started, requiring a fresh trigger word on every single reply doesn't
    match how people actually type a follow-up. Any leading "?" is still
    stripped since it's evidently a habit, but it's cosmetic here, not a
    gate.
    """
    stripped = text.strip().lstrip("?").strip()
    core_prefix = COMMAND_PREFIX.lstrip("?")
    if stripped.lower().startswith(core_prefix):
        stripped = stripped[len(core_prefix):].strip()
    return stripped or None


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

MAX_AUTO_HORIZON_DAYS = 120  # cap how far a detected date can widen the fetch -- avoid pulling months of thin-liquidity expirations off one offhand date mention


def detect_target_horizon_days(question: str, today: datetime.date | None = None) -> int | None:
    """Scans a follow-up question for an explicit date (e.g. "Sept 18th
    2026", "9/18/2026") and, if found beyond the default two-week window,
    returns how many days out to fetch instead so that expiration is
    actually included.

    Exists because of a real, observed failure: someone asked "?ask SOXL
    sell put Sept18th 2026" and got a confident-sounding answer built
    entirely from data through Aug 7 -- the default 14-day window doesn't
    reach a September date at all, and nothing told the user that. This
    doesn't try to be a general date parser (no relative phrases like
    "next month" or "in 6 weeks") -- just the concrete pattern that
    already broke once. A missed date still falls through to the default
    window rather than erroring, since a best-effort near-term answer
    beats no answer.
    """
    today = today or datetime.date.today()
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
            # No year given and the date's already past this year -- assume next year.
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
    days_out = (target - today).days
    if days_out <= ask_lib.DEFAULT_HORIZON_DAYS:
        return None  # already covered by the default window
    # +7 day buffer: the mentioned date isn't necessarily itself a real
    # expiration (options expire on specific weekdays), so widen slightly
    # past it to make sure the nearest real expiration on/after it is included.
    return min(days_out + 7, MAX_AUTO_HORIZON_DAYS)


# Slack's chat.postMessage `text` field renders Slack's own "mrkdwn", not
# standard Markdown: *single asterisks* for bold, no "#" header syntax,
# and no native bullet rendering (a "- " or "* " just shows as a literal
# character). A model that writes normal Markdown -- which is what most
# are trained on -- produces visibly broken output in Slack ("**Task
# 1**" shows literal asterisks). Tell it explicitly, and back that up with
# to_slack_mrkdwn() below as a safety net, since a 3B local model won't
# always follow formatting instructions precisely.
SLACK_FORMATTING_INSTRUCTIONS = (
    "\n\nFORMAT your answer for Slack, not standard Markdown: use *single asterisks* for bold "
    "(never **double asterisks**), use plain dashes or the bullet character • for lists "
    "(never # or ## headers), and keep paragraphs short."
)


def build_answer_prompt(ticker: str, extra_question: str) -> tuple[str, dict]:
    profile = ask_lib.resolve_ticker(ticker)
    name = profile.get("name") or ticker

    # If the question names a specific date beyond the default two-week
    # window (e.g. "sell put Sept 18th 2026"), widen the fetch to actually
    # include it -- see detect_target_horizon_days's docstring for the
    # real failure this fixes: the default window silently didn't cover a
    # September question and nothing said so.
    horizon = detect_target_horizon_days(extra_question) if extra_question else None
    horizon = horizon or ask_lib.DEFAULT_HORIZON_DAYS
    window = ask_lib.fetch_json(
        f"{ask_lib.BACKEND_URL}/api/companies/{ticker}/options/two-week?horizon_days={horizon}"
    )
    prompt = ask_lib.build_prompt(ticker, name, window)
    if extra_question:
        prompt += (
            f"\n\nADDITIONAL QUESTION FROM THE USER: {extra_question}\n"
            "Address this directly as part of your answer, still grounded only in the data above. "
            "The DATA above covers every expiration through "
            f"{window['expirations'][-1]['expiration_date'] if window.get('expirations') else 'the near term'} "
            "only -- if the question is about a date beyond that, say so explicitly rather than answering "
            "as if it were covered."
        )
    prompt += SLACK_FORMATTING_INSTRUCTIONS
    return prompt, {"name": name, "window": window, "horizon_days": horizon}


def to_slack_mrkdwn(text: str) -> str:
    """Best-effort cleanup of standard Markdown into real Slack mrkdwn --
    a safety net for when the model doesn't fully follow
    SLACK_FORMATTING_INSTRUCTIONS above. Not a full parser, just the
    patterns actually observed in local-model output: "# " headers,
    "**bold**", and "* "/"+ " bullets."""
    lines = []
    for line in text.split("\n"):
        header = re.match(r"^(#{1,6})\s+(.*)$", line)
        if header:
            lines.append(f"*{header.group(2).strip()}*")
            continue
        bullet = re.match(r"^(\s*)[*+]\s+(.*)$", line)
        if bullet:
            lines.append(f"{bullet.group(1)}• {bullet.group(2)}")
            continue
        lines.append(line)
    result = "\n".join(lines)
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)


def handle_command(channel_id: str, ticker: str, extra_question: str, thread_ts: str) -> str | None:
    """Returns the ticker on success (so poll_once can remember it as this
    thread's context for follow-up replies), None on any failure."""
    print(f"[{time.strftime('%H:%M:%S')}] handling ?ask {ticker} {extra_question!r}")
    try:
        post_message(channel_id, f"\U0001f914 Looking at {ticker}... (local Ollama, can take a few minutes)", thread_ts=thread_ts)
    except RuntimeError as exc:
        print(f"  warning: couldn't post ack: {exc}")

    try:
        prompt, meta = build_answer_prompt(ticker, extra_question)
    except SystemExit as exc:
        post_message(channel_id, f"Couldn't resolve `{ticker}`: {exc}", thread_ts=thread_ts)
        return None
    except (urllib.error.URLError, RuntimeError) as exc:
        post_message(channel_id, f"Couldn't fetch data for `{ticker}`: {exc}", thread_ts=thread_ts)
        return None

    added = ask_lib.add_to_watchlist(ticker)
    link = f"{ask_lib.FRONTEND_URL}/c/{ticker}/options"

    try:
        answer = ask_lib.run_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, timeout=600)
    except SystemExit as exc:
        post_message(channel_id, f"Local Ollama couldn't answer this one: {exc}", thread_ts=thread_ts)
        return None

    answer = to_slack_mrkdwn(answer.strip())
    if len(answer) > MAX_REPLY_CHARS:
        answer = answer[:MAX_REPLY_CHARS] + "\n\n_(truncated)_"

    watchlist_note = f"\n_Added {ticker} to the daily scan watchlist._" if added else ""
    horizon_note = (
        f"\n_Widened the window to {meta['horizon_days']} days to cover the date in your question._"
        if meta.get("horizon_days", ask_lib.DEFAULT_HORIZON_DAYS) > ask_lib.DEFAULT_HORIZON_DAYS
        else ""
    )
    reply = f"*{meta['name']} ({ticker})* — {link}\n\n{answer}{watchlist_note}{horizon_note}"
    post_message(channel_id, reply, thread_ts=thread_ts)
    print(f"  done, posted {len(answer)} chars")
    return ticker


def poll_once(channel_id: str, bot_user_id: str, state: dict) -> None:
    params = {"channel": channel_id, "limit": 50}
    if state.get("last_ts"):
        params["oldest"] = state["last_ts"]
    data = slack_call("conversations.history", params, http_method="GET")
    messages = sorted(data.get("messages", []), key=lambda m: float(m["ts"]))

    thread_tickers = state.setdefault("thread_tickers", {})

    newest_ts = state.get("last_ts")
    for msg in messages:
        ts = msg["ts"]
        if state.get("last_ts") and float(ts) <= float(state["last_ts"]):
            continue
        newest_ts = ts if not newest_ts or float(ts) > float(newest_ts) else newest_ts

        if msg.get("bot_id") or msg.get("user") == bot_user_id or msg.get("subtype"):
            continue

        is_reply = bool(msg.get("thread_ts") and msg["thread_ts"] != ts)
        text = msg.get("text", "")
        ticker: str | None = None
        extra = ""
        reply_target = msg.get("thread_ts") or ts

        if is_reply and thread_tickers.get(msg["thread_ts"]):
            # A follow-up in a thread this bot already answered in --
            # inherit that thread's ticker rather than re-parsing one, so
            # natural phrasing like "...what about it on the call side"
            # (no ticker as the first word) still works. See
            # parse_reply_question's docstring for why this exists.
            question = parse_reply_question(text)
            if question is not None:
                ticker, extra = thread_tickers[msg["thread_ts"]], question
        if ticker is None:
            # Either a top-level message, or a reply in a thread with no
            # known ticker yet -- require the strict "ask TICKER ..." form
            # so a random threaded aside doesn't misfire.
            parsed = parse_command(text)
            if parsed:
                ticker, extra = parsed

        if ticker is None:
            continue

        try:
            resolved = handle_command(channel_id, ticker, extra, thread_ts=reply_target)
        except Exception:
            resolved = None
            traceback.print_exc()
            try:
                post_message(channel_id, "Something went wrong answering that one -- check the bot's local log.", thread_ts=reply_target)
            except RuntimeError:
                pass
        if resolved:
            thread_tickers[reply_target] = resolved

    if newest_ts:
        state["last_ts"] = newest_ts
        save_state(state)


def main() -> None:
    once = "--once" in sys.argv
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL:
        raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_CHANNEL (env vars or scripts/.env) -- see scripts/.env.example")

    channel_id = resolve_channel_id(SLACK_CHANNEL)
    bot_user_id = slack_call("auth.test", {}, http_method="POST")["user_id"]
    print(f"Connected as {bot_user_id}, watching channel {channel_id} for '{COMMAND_PREFIX} TICKER'")

    state = load_state()
    if "last_ts" not in state:
        # First run: don't answer the channel's entire history, only
        # messages from now on.
        state["last_ts"] = str(time.time())
        save_state(state)
        print("First run -- baselined to now, will only answer new messages.")

    if once:
        poll_once(channel_id, bot_user_id, state)
        return

    while True:
        try:
            poll_once(channel_id, bot_user_id, state)
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
