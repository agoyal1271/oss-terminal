#!/usr/bin/env python3
"""Local Slack Q&A daemon for #equity-alerts -- polls the channel for
`?ask TICKER [optional question]`, answers using local Ollama, posts the
reply back in a thread. Runs entirely on this laptop; nobody's Claude
account is involved, and nothing is exposed to the public internet -- it's
an outbound-polling client, not a server.

Reuses every piece of scripts/ask.py (ticker resolution, the Ollama call,
and both prompts it builds) rather than reimplementing any of it, so the
Slack answer and the terminal answer are always the same logic. A bare
`?ask TICKER` gets the fast classify-into-up/down/sideways-and-flag-
unusual-activity prompt (two-week window). `?ask TICKER <question>` (or
any reply in a thread the bot already answered in) instead goes through
ask.py's build_strategy_prompt -- base/bull/bear price outlook, what the
options chain implies, IV rank, liquid strikes/expirations, delta/theta/
OI, probability of profit, 5/10/15% sensitivity, event risk, premium vs.
spread -- for any ticker and whatever horizon(s) the question names (or a
~3/~6 month default if it names none; the answer says so explicitly and
invites a correction rather than guessing silently).

Config via environment variables (or a `.env` file next to this script --
see scripts/.env.example). Required:
  SLACK_BOT_TOKEN   Bot User OAuth Token (starts xoxb-), scopes needed:
                     channels:history, channels:read, chat:write
                     (add groups:history/groups:read too if the channel is
                     private) -- and the bot must be INVITED to the channel.
  SLACK_CHANNEL     channel ID (C...) or name (#equity-alerts / equity-alerts)

Optional (defaults shown):
  OLLAMA_URL=http://localhost:11434
  OLLAMA_MODEL=martain7r/finance-llama-8b:q4_k_m
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
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "martain7r/finance-llama-8b:q4_k_m")
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


# Horizon/date parsing ("Sept 18th 2026", "3 months", "3- to 6-month") lives
# in ask.py's parse_horizons_days now, shared with the CLI's --question flag,
# so a date mentioned in a Slack thread and the same date typed at the
# terminal resolve to the same day count rather than two regimes drifting
# apart. See ask.py's parse_horizons_days docstring for what it does and
# deliberately doesn't attempt.


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
    """Bare `?ask TICKER` (no question) keeps the fast classify-and-flag
    path: fetch the two-week window, build_prompt. A real follow-up
    question -- which is most of what actually gets asked in a thread --
    instead goes through ask_lib.build_strategy_prompt, which can answer
    price-outlook, options-chain, Greeks/POP, sensitivity, and event-risk
    questions for any horizon(s) the question names (see its docstring).
    Both paths are ask.py's logic, reused as-is, so the Slack answer and
    the terminal answer are never two different code paths."""
    profile = ask_lib.resolve_ticker(ticker)
    name = profile.get("name") or ticker

    if extra_question:
        prompt, meta = ask_lib.build_strategy_prompt(ticker, name, extra_question)
        prompt += SLACK_FORMATTING_INSTRUCTIONS
        return prompt, meta

    window = ask_lib.fetch_json(
        f"{ask_lib.BACKEND_URL}/api/companies/{ticker}/options/two-week?horizon_days={ask_lib.DEFAULT_HORIZON_DAYS}"
    )
    prompt = ask_lib.build_prompt(ticker, name, window)
    prompt += SLACK_FORMATTING_INSTRUCTIONS
    return prompt, {"name": name, "window": window, "horizon_days": ask_lib.DEFAULT_HORIZON_DAYS}


# Deterministic backstop for the "no advice" prompt rule -- added after
# observing TWO different models violate it despite the exact same
# explicit, twice-repeated instruction: llama3.2 invented options Greeks
# that weren't in that prompt's data, and finance-llama-8b (ironically,
# given its finance fine-tuning) told the user "it may be a wise decision
# to sell puts" -- an outright trade recommendation. Prompt wording alone
# clearly isn't reliable enough; this scans the actual output before it
# gets posted and flags it visibly rather than silently trusting it.
#
# Split into two tiers because build_strategy_prompt (ask.py) changed what
# "off-rules" means: the classify-and-flag prompt (build_prompt) never
# provides Greeks or names a strategy, so ANY mention there is invented.
# build_strategy_prompt deliberately DOES compute and expect both --
# "long call", "call debit spread", real delta/theta values -- as the
# answer to what was asked. Running the strategy-name/Greeks patterns
# against a strategy answer would flag nearly every legitimate response,
# which defeats the backstop through alarm fatigue rather than serving it.
# An explicit recommendation ("you should...", "I suggest...") is still
# never acceptable from either prompt, so that tier always applies.
_RECOMMENDATION_PATTERNS = [
    re.compile(r"\b(I recommend|I suggest|you should|it('s| is)? (a )?(wise|good|smart) (decision|idea|move|trade|strategy)|"
               r"consider (buying|selling))\b", re.IGNORECASE),
]
_STRATEGY_NAME_PATTERNS = [
    re.compile(r"\b(buy|sell|long|short|go long|go short)\s+(a\s+|the\s+)?(call|put)s?\b", re.IGNORECASE),
    re.compile(r"\b(covered call|protective collar|bear put spread|bull call spread|iron condor|"
               r"credit spread|debit spread|calendar spread|straddle|strangle)\b", re.IGNORECASE),
]
_GREEKS_PATTERN = re.compile(r"\b(delta|gamma|theta|vega)\b", re.IGNORECASE)


def check_advice_violations(text: str, is_strategy_answer: bool = False) -> list[str]:
    """Returns a list of human-readable reasons if `text` looks like it
    broke a no-advice rule, else an empty list. Not a guarantee of
    catching everything (regex over free text never is), but a real
    backstop for the concrete failure modes already observed live -- see
    the comment above. `is_strategy_answer` should be True when the
    answer came from build_strategy_prompt (its meta dict has
    "horizons_days") -- Greeks and strategy names are expected there and
    are only flagged for the classify-and-flag prompt's answers."""
    reasons = []
    for pattern in _RECOMMENDATION_PATTERNS:
        if pattern.search(text):
            reasons.append("possible trade recommendation")
            break
    if not is_strategy_answer:
        for pattern in _STRATEGY_NAME_PATTERNS:
            if pattern.search(text):
                reasons.append("mentions a specific options strategy (never suggested in this prompt's data)")
                break
        if _GREEKS_PATTERN.search(text):
            reasons.append("mentions options Greeks (never provided in this prompt's data)")
    return reasons


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

    is_strategy_answer = "horizons_days" in meta  # build_strategy_prompt's meta shape vs. build_prompt's
    violations = check_advice_violations(answer, is_strategy_answer=is_strategy_answer)
    answer = to_slack_mrkdwn(answer.strip())
    if len(answer) > MAX_REPLY_CHARS:
        answer = answer[:MAX_REPLY_CHARS] + "\n\n_(truncated)_"

    watchlist_note = f"\n_Added {ticker} to the daily scan watchlist._" if added else ""
    if is_strategy_answer:
        horizons_str = ", ".join(f"~{d}d" for d in meta["horizons_days"])
        assumed_str = " (assumed -- none was named in the question, ask again with a specific timeframe to change it)" if meta.get("horizons_assumed") else ""
        horizon_note = f"\n_Horizon(s) used: {horizons_str}{assumed_str}._"
    else:
        horizon_note = (
            f"\n_Widened the window to {meta['horizon_days']} days to cover the date in your question._"
            if meta.get("horizon_days", ask_lib.DEFAULT_HORIZON_DAYS) > ask_lib.DEFAULT_HORIZON_DAYS
            else ""
        )
    warning = (
        f"⚠️ *This response may not follow house style* ({'; '.join(violations)}) -- "
        "this is a research tool, not investment advice; treat any strategy language or figures not "
        "explicitly listed above with extra caution.\n\n"
        if violations
        else ""
    )
    reply = f"{warning}*{meta['name']} ({ticker})* — {link}\n\n{answer}{watchlist_note}{horizon_note}"
    post_message(channel_id, reply, thread_ts=thread_ts)
    if violations:
        print(f"  WARNING: flagged output -- {violations}")
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
