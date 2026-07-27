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
    return prompt, {"name": name, "window": window}


def handle_command(channel_id: str, ticker: str, extra_question: str, thread_ts: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] handling ?ask {ticker} {extra_question!r}")
    try:
        post_message(channel_id, f"\U0001f914 Looking at {ticker}... (local Ollama, can take a few minutes)", thread_ts=thread_ts)
    except RuntimeError as exc:
        print(f"  warning: couldn't post ack: {exc}")

    try:
        prompt, meta = build_answer_prompt(ticker, extra_question)
    except SystemExit as exc:
        post_message(channel_id, f"Couldn't resolve `{ticker}`: {exc}", thread_ts=thread_ts)
        return
    except (urllib.error.URLError, RuntimeError) as exc:
        post_message(channel_id, f"Couldn't fetch data for `{ticker}`: {exc}", thread_ts=thread_ts)
        return

    added = ask_lib.add_to_watchlist(ticker)
    link = f"{ask_lib.FRONTEND_URL}/c/{ticker}/options"

    try:
        answer = ask_lib.run_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, timeout=600)
    except SystemExit as exc:
        post_message(channel_id, f"Local Ollama couldn't answer this one: {exc}", thread_ts=thread_ts)
        return

    if len(answer) > MAX_REPLY_CHARS:
        answer = answer[:MAX_REPLY_CHARS] + "\n\n_(truncated)_"

    watchlist_note = f"\n_Added {ticker} to the daily scan watchlist._" if added else ""
    reply = f"*{meta['name']} ({ticker})* — {link}\n\n{answer}{watchlist_note}"
    post_message(channel_id, reply, thread_ts=thread_ts)
    print(f"  done, posted {len(answer)} chars")


def poll_once(channel_id: str, bot_user_id: str, state: dict) -> None:
    params = {"channel": channel_id, "limit": 50}
    if state.get("last_ts"):
        params["oldest"] = state["last_ts"]
    data = slack_call("conversations.history", params, http_method="GET")
    messages = sorted(data.get("messages", []), key=lambda m: float(m["ts"]))

    newest_ts = state.get("last_ts")
    for msg in messages:
        ts = msg["ts"]
        if state.get("last_ts") and float(ts) <= float(state["last_ts"]):
            continue
        newest_ts = ts if not newest_ts or float(ts) > float(newest_ts) else newest_ts

        if msg.get("bot_id") or msg.get("user") == bot_user_id or msg.get("subtype"):
            continue
        if msg.get("thread_ts") and msg["thread_ts"] != ts:
            continue  # only react to new top-level messages, not thread replies

        parsed = parse_command(msg.get("text", ""))
        if not parsed:
            continue
        ticker, extra = parsed
        try:
            handle_command(channel_id, ticker, extra, thread_ts=ts)
        except Exception:
            traceback.print_exc()
            try:
                post_message(channel_id, f"Something went wrong answering that one -- check the bot's local log.", thread_ts=ts)
            except RuntimeError:
                pass

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
