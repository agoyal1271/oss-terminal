"""Unit tests for the deterministic covered-call screen.

Network access is intentionally absent: live Yahoo behavior is exercised by
the CLI, while these tests pin the safety-critical filtering and sizing rules.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import covered_call  # noqa: E402
import ask  # noqa: E402
import slack_bot  # noqa: E402


def _expiration(days: int) -> int:
    target = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=days)
    return int(dt.datetime.combine(target, dt.time(), tzinfo=dt.timezone.utc).timestamp())


def _call(strike: float, *, bid: float = 1.0, ask: float = 1.08, oi: int = 500, volume: int = 100) -> dict:
    return {
        "contract_symbol": f"TEST{strike}",
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "open_interest": oi,
        "volume": volume,
        "implied_volatility": 0.55,
    }


def test_screen_rejects_illiquid_wide_and_below_cost_basis_calls():
    expiration = _expiration(30)
    chain = {
        "selected_expiration": expiration,
        "calls": [
            _call(105),  # below supplied basis
            _call(115, oi=20),  # illiquid
            _call(120, bid=0.30, ask=0.70),  # wide
            _call(110),  # valid
        ],
    }

    candidates, rejected = covered_call.screen_candidates(
        [chain],
        spot=100,
        cost_basis=108,
        min_delta=0,
        max_delta=1,
    )

    assert [candidate["strike"] for candidate in candidates] == [110]
    assert rejected["below_cost_basis"] == 1
    assert rejected["liquidity"] == 1
    assert rejected["spread"] == 1


def test_position_plan_respects_core_and_assignment_cap():
    candidate = {
        "contract_symbol": "TESTCALL",
        "bid": 1.25,
        "strike": 35.0,
        "return_if_called_from_cost_basis": 0.45,
    }
    plan = covered_call.position_plan(
        shares=700,
        cost_basis=25,
        protected_core_shares=300,
        max_assignment_shares=200,
        candidate=candidate,
    )

    assert plan["actionable"] is True
    assert plan["max_covered_call_contracts"] == 2
    assert plan["eligible_covered_shares"] == 200
    assert plan["gross_bid_credit"] == 250
    assert plan["effective_exit_price_if_called"] == 36.25


def test_position_plan_is_not_actionable_without_private_position_data():
    plan = covered_call.position_plan(
        shares=None,
        cost_basis=None,
        protected_core_shares=0,
        max_assignment_shares=None,
        candidate=None,
    )

    assert plan["actionable"] is False
    assert plan["missing"] == ["shares", "cost_basis"]


def test_llm_prompt_preserves_non_actionable_position_boundary():
    result = {
        "ticker": "TEST",
        "source": {"provider": "Yahoo"},
        "top_market_candidates": [],
        "position_plan": {"actionable": False, "missing": ["shares", "cost_basis"]},
    }
    prompt = covered_call.build_llm_prompt(result, "covered call?")

    assert "authoritative for option quotes" in prompt
    assert "additional public research" in prompt
    assert "exact contracts cannot be selected" in prompt
    assert '"actionable": false' in prompt


def test_slack_covered_call_question_routes_to_same_screen(monkeypatch):
    monkeypatch.setattr(slack_bot.ask_lib, "resolve_ticker", lambda ticker: {"name": "Test Co"})
    monkeypatch.setattr(slack_bot.covered_call_lib, "analyze", lambda ticker: {"ticker": ticker})
    monkeypatch.setattr(
        slack_bot.covered_call_lib,
        "build_llm_prompt",
        lambda result, question: f"covered-call prompt for {result['ticker']}: {question}",
    )

    prompt, meta = slack_bot.build_answer_prompt("TEST", "compare covered calls")

    assert prompt.startswith("covered-call prompt for TEST")
    assert meta["answer_type"] == "covered_call"


def test_public_channel_resolution_does_not_require_private_scope(monkeypatch):
    calls = []

    def fake_slack_call(method, params, http_method="POST"):
        calls.append(params["types"])
        if params["types"] == "public_channel":
            return {"channels": [{"id": "C123", "name": "equity-alerts"}]}
        raise RuntimeError("Slack API conversations.list failed: missing_scope")

    monkeypatch.setattr(slack_bot, "slack_call", fake_slack_call)

    assert slack_bot.resolve_channel_id("equity-alerts") == "C123"
    assert calls == ["public_channel"]


def test_openai_web_search_is_optional_and_citations_are_rendered(monkeypatch):
    captured = {}
    api_response = {
        "id": "resp_test",
        "output": [{
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": "A public event may matter.",
                "annotations": [{
                    "type": "url_citation",
                    "title": "SEC filing",
                    "url": "https://www.sec.gov/example",
                }],
            }],
        }],
    }

    def fake_urlopen(request, timeout=0):
        captured["payload"] = json.loads(request.data)
        return io.BytesIO(json.dumps(api_response).encode())

    monkeypatch.setattr(ask.urllib.request, "urlopen", fake_urlopen)
    answer = ask.run_openai("analyze", "test-model", api_key="test-key")

    assert captured["payload"]["tools"] == [{
        "type": "web_search",
        "search_context_size": "medium",
        "external_web_access": True,
    }]
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["store"] is False
    assert "SEC filing: https://www.sec.gov/example" in answer


def test_openai_web_search_can_be_disabled(monkeypatch):
    captured = {}
    api_response = {
        "id": "resp_test",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
    }

    def fake_urlopen(request, timeout=0):
        captured["payload"] = json.loads(request.data)
        return io.BytesIO(json.dumps(api_response).encode())

    monkeypatch.setattr(ask.urllib.request, "urlopen", fake_urlopen)
    ask.run_openai("analyze", "test-model", api_key="test-key", enable_web_search=False)

    assert "tools" not in captured["payload"]


def test_slack_truncation_preserves_web_sources():
    answer = "A" * 5000 + "\n\nSources:\n- SEC filing: https://www.sec.gov/example"

    truncated = slack_bot.truncate_for_slack(answer, limit=500)

    assert len(truncated) <= 500
    assert "analysis truncated; sources preserved" in truncated
    assert "https://www.sec.gov/example" in truncated
