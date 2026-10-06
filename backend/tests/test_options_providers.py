from __future__ import annotations

import datetime

from app.ingest import options


def test_tradier_contract_normalization_includes_greeks_and_timestamps():
    raw = {
        "symbol": "TEST270115C00105000",
        "option_type": "call",
        "strike": 105,
        "last": 2.0,
        "bid": 1.9,
        "ask": 2.1,
        "change": 0.2,
        "change_percentage": 11.1,
        "volume": 125,
        "open_interest": 500,
        "bid_date": 1_800_000_000_000,
        "ask_date": 1_800_000_001_000,
        "trade_date": 1_800_000_002_000,
        "greeks": {
            "mid_iv": 0.31,
            "delta": 0.29,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.08,
            "rho": 0.01,
            "updated_at": "2027-01-01 15:59:00",
        },
    }

    normalized = options._normalize_tradier_contract(raw, underlying_price=100)

    assert normalized["contract_symbol"] == raw["symbol"]
    assert normalized["implied_volatility"] == 0.31
    assert normalized["delta"] == 0.29
    assert normalized["theta"] == -0.05
    assert normalized["in_the_money"] is False
    assert normalized["quote_at"].startswith("2027-")


def test_tradier_chain_preserves_existing_public_shape(monkeypatch):
    expiration = int(
        datetime.datetime(2027, 1, 15, tzinfo=datetime.timezone.utc).timestamp()
    )
    monkeypatch.setattr(options, "_tradier_expirations", lambda ticker: ([expiration], "2027-01-01T20:00:00+00:00"))
    monkeypatch.setattr(
        options,
        "_tradier_quote",
        lambda ticker: (
            {"last": 100, "bid_date": 1_800_000_000_000, "ask_date": 1_800_000_001_000},
            "2027-01-01T20:00:01+00:00",
        ),
    )

    contracts = []
    for side in ("call", "put"):
        contracts.append(
            {
                "symbol": f"TEST-{side}",
                "option_type": side,
                "strike": 100,
                "last": 3,
                "bid": 2.9,
                "ask": 3.1,
                "volume": 100,
                "open_interest": 200,
                "greeks": {"mid_iv": 0.30, "delta": 0.5 if side == "call" else -0.5},
            }
        )

    monkeypatch.setattr(
        options,
        "cached_call_json",
        lambda **kwargs: {
            "captured_at_utc": "2027-01-01T20:00:02+00:00",
            "payload": {"options": {"option": contracts}},
        },
    )

    chain = options._get_tradier_options_chain("test", expiration)

    assert chain["source"] == "tradier"
    assert chain["feed_status"] == "realtime_production"
    assert chain["selected_expiration"] == expiration
    assert len(chain["calls"]) == 1
    assert len(chain["puts"]) == 1
    assert chain["summary"]["expected_move_atm_straddle"] == 6


def test_auto_provider_prefers_tradier_when_token_exists(monkeypatch):
    monkeypatch.setattr(options.settings, "options_provider", "auto")
    monkeypatch.setattr(options.settings, "tradier_token", "secret")
    assert options._selected_provider() == "tradier"


def test_auto_provider_falls_back_without_token(monkeypatch):
    monkeypatch.setattr(options.settings, "options_provider", "auto")
    monkeypatch.setattr(options.settings, "tradier_token", None)
    assert options._selected_provider() == "yahoo"
