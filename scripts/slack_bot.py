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
    """Returns the question text if `text` starts with the command prefix
    (with or without leading "?"), else None -- deliberately does NOT
    require a ticker-shaped first word, unlike parse_command.

    For a threaded follow-up, forcing "ticker is the first word after
    ask" breaks on completely normal phrasing -- observed live: "?ask does
    it carry more weight on the call side or put side with 140% IV on
    spcx" has the ticker at the END of the sentence, and a naive first-word
    parse would try to resolve "DOES" as a ticker and fail. Used only for
    replies inside a thread that already has an established ticker (see
    poll_once), so there's no ambiguity about what stock the question is
    about -- the whole remainder is just the follow-up question.
    """
    stripped = text.strip().lstrip("?").strip()
    core_prefix = COMMAND_PREFIX.lstrip("?")
    if not stripped.lower().startswith(core_prefix):
        return None
    rest = stripped[len(core_prefix):].strip()
    return rest or None


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
    window = ask_lib.fetch_json(
        f"{ask_lib.BACKEND_URL}/api/companies/{ticker}/options/two-week?horizon_days={ask_lib.DEFAULT_HORIZON_DAYS}"
    )
    prompt = ask_lib.build_prompt(ticker, name, window)
    if extra_question:
        prompt += (
            f"\n\nADDITIONAL QUESTION FROM THE USER: {extra_question}\n"
            "Address this directly as part of your answer, still grounded only in the data above -- "
            "if the data above doesn't cover it, say so rather than guessing."
        )
    prompt += SLACK_FORMATTING_INSTRUCTIONS
    return prompt, {"name": name, "window": window}


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
    reply = f"*{meta['name']} ({ticker})* — {link}\n\n{answer}{watchlist_note}"
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
