"""Tests for the Bitget exchange adapter (REST + signing + runner).

Uses a mocked `requests.Session` so the suite runs offline. We verify:
  - Public endpoints route correctly and parse Bitget's `{code, msg, data}` envelope
  - Signed endpoints produce the right HMAC + headers
  - Retry/backoff on 429 / 5xx
  - `BitgetBotRunner.place()` blocks on risk violations and is no-op when simulated
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from copilot.exchanges import (
    BitgetBotRunner,
    BitgetClient,
    BitgetCredentials,
)
from copilot.exchanges.bitget_client import BitgetAPIError, public_ws_subscribe_instructions
from copilot.risk_engine import RiskEngine


RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "default.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_response(status_code: int, payload: dict):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


def _ok(data):
    return _mock_response(200, {"code": "00000", "msg": "success", "data": data})


# ---------------------------------------------------------------------------
# Public endpoints (no credentials)
# ---------------------------------------------------------------------------
def test_get_server_time_parses_envelope():
    session = MagicMock()
    session.request.return_value = _ok({"serverTime": "1700000000000"})
    client = BitgetClient(session=session)
    ts = client.get_server_time()
    assert ts["serverTime"] == "1700000000000"
    # Verify the URL called
    kwargs = session.request.call_args.kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["url"].endswith("/api/v2/public/time")
    assert kwargs["headers"]["Content-Type"] == "application/json"


def test_get_tickers_returns_list():
    session = MagicMock()
    session.request.return_value = _ok([
        {"symbol": "BTCUSDT", "lastPr": "65000", "bidPr": "64999", "askPr": "65001"},
        {"symbol": "ETHUSDT", "lastPr": "3200",  "bidPr": "3199",  "askPr": "3201"},
    ])
    client = BitgetClient(session=session)
    tickers = client.get_tickers("SPOT")
    assert isinstance(tickers, list)
    assert {t["symbol"] for t in tickers} == {"BTCUSDT", "ETHUSDT"}


def test_get_candles_passes_params():
    session = MagicMock()
    session.request.return_value = _ok([["1700000000000", "65000", "65100", "64900", "65050", "100"]])
    client = BitgetClient(session=session)
    candles = client.get_candles("BTCUSDT", granularity="1m", limit=1)
    assert len(candles) == 1
    kwargs = session.request.call_args.kwargs
    # URL has the query string
    assert "symbol=BTCUSDT" in kwargs["url"]
    assert "granularity=1m" in kwargs["url"]


# ---------------------------------------------------------------------------
# Signed endpoints
# ---------------------------------------------------------------------------
def test_signed_endpoint_produces_correct_signature():
    session = MagicMock()
    session.request.return_value = _ok([{"asset": "USDT", "available": "1234.56"}])
    creds = BitgetCredentials(api_key="ak", api_secret="sk", passphrase="pp", demo=True)
    client = BitgetClient(credentials=creds, session=session)
    # Pin the timestamp so the signature is deterministic
    with patch.object(client, "_timestamp_ms", return_value="1700000000000"):
        client.get_account("spot")

    kwargs = session.request.call_args.kwargs
    method = kwargs["method"]
    url = kwargs["url"]
    headers = kwargs["headers"]
    body = kwargs.get("data") or ""

    assert method == "GET"
    assert "ACCESS-KEY" in headers and headers["ACCESS-KEY"] == "ak"
    assert headers["ACCESS-TIMESTAMP"] == "1700000000000"
    assert headers["ACCESS-PASSPHRASE"] == "pp"
    assert headers.get("x-simulated-trading") == "1"

    # Reproduce the expected signature
    path = "/api/v2/account/account"
    qs = url.split("?", 1)[1] if "?" in url else ""
    expected_prehash = "1700000000000" + "GET" + path + (("?" + qs) if qs else "") + (body or "")
    expected = base64.b64encode(
        hmac.new(b"sk", expected_prehash.encode(), hashlib.sha256).digest()
    ).decode()
    assert headers["ACCESS-SIGN"] == expected


def test_place_order_requires_limit_price():
    creds = BitgetCredentials(api_key="ak", api_secret="sk", passphrase="pp")
    client = BitgetClient(credentials=creds)
    with pytest.raises(BitgetAPIError):
        client.place_order("BTCUSDT", "BUY", "LIMIT", size="0.001")  # no price


def test_place_order_happy_path():
    session = MagicMock()
    session.request.return_value = _ok({"orderId": "12345", "clientOid": "abc"})
    creds = BitgetCredentials(api_key="ak", api_secret="sk", passphrase="pp")
    client = BitgetClient(credentials=creds, session=session)
    resp = client.place_order("BTCUSDT", "BUY", "LIMIT", size="0.001", price="65000")
    assert resp["orderId"] == "12345"
    kwargs = session.request.call_args.kwargs
    body = json.loads(kwargs["data"])
    assert body["symbol"] == "BTCUSDT"
    assert body["orderType"] == "limit"
    assert body["size"] == "0.001"
    assert body["price"] == "65000"
    assert "ACCESS-SIGN" in kwargs["headers"]


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------
def test_retries_on_429():
    session = MagicMock()
    # First two calls 429, third succeeds
    session.request.side_effect = [
        _mock_response(429, {"code": "429", "msg": "rate"}),
        _mock_response(429, {"code": "429", "msg": "rate"}),
        _ok([{"ok": True}]),
    ]
    client = BitgetClient(session=session, max_retries=3)
    # Avoid sleeping in tests
    with patch.object(client, "_sleep_backoff", lambda *a, **k: None):
        data = client.get_tickers("SPOT")
    assert data == [{"ok": True}]
    assert session.request.call_count == 3


def test_raises_on_persistent_error():
    session = MagicMock()
    session.request.return_value = _mock_response(500, {"code": "500", "msg": "boom"})
    client = BitgetClient(session=session, max_retries=2)
    with patch.object(client, "_sleep_backoff", lambda *a, **k: None):
        with pytest.raises(BitgetAPIError):
            client.get_tickers("SPOT")
    assert session.request.call_count == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# WebSocket helper
# ---------------------------------------------------------------------------
def test_public_ws_subscribe_instructions():
    frame = public_ws_subscribe_instructions(
        [("ticker", "BTCUSDT"), ("candle5m", "ETHUSDT")],
        product_type="SPOT",
    )
    parsed = json.loads(frame)
    assert parsed["op"] == "subscribe"
    args = parsed["args"]
    assert args[0] == {"instType": "SPOT", "channel": "ticker", "instId": "BTCUSDT"}
    assert args[1] == {"instType": "SPOT", "channel": "candle5m", "instId": "ETHUSDT"}


# ---------------------------------------------------------------------------
# BitgetBotRunner - risk-gated order placement
# ---------------------------------------------------------------------------
def test_bot_runner_blocks_oversized_order():
    client = MagicMock(spec=BitgetClient)
    risk = RiskEngine(str(RULES_PATH))
    runner = BitgetBotRunner(client, risk, simulated=True)
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=1.0, price=50000)
    assert not res.allowed
    assert res.rule_violated == "max_position_size"
    client.place_order.assert_not_called()


def test_bot_runner_simulated_does_not_call_exchange():
    client = MagicMock(spec=BitgetClient)
    risk = RiskEngine(str(RULES_PATH))
    runner = BitgetBotRunner(client, risk, simulated=True)
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001, price=50000)
    assert res.allowed
    assert res.submitted is True
    assert res.order_response["simulated"] is True
    client.place_order.assert_not_called()


def test_bot_runner_live_calls_exchange_when_allowed():
    client = MagicMock(spec=BitgetClient)
    client.place_order.return_value = {"orderId": "999"}
    risk = RiskEngine(str(RULES_PATH))
    runner = BitgetBotRunner(client, risk, simulated=False)
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001,
                       price=50000, order_type="LIMIT")
    assert res.allowed and res.submitted
    assert res.order_response["orderId"] == "999"
    client.place_order.assert_called_once()


def test_bot_runner_normalizes_symbol():
    client = MagicMock(spec=BitgetClient)
    risk = RiskEngine(str(RULES_PATH))
    runner = BitgetBotRunner(client, risk, simulated=True)
    # Use a symbol format that needs normalization (BTCUSDT -> BTC/USDT)
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001, price=50000)
    assert res.allowed


def test_bot_runner_catches_api_errors_in_live_mode():
    from copilot.exchanges.bitget_client import BitgetAPIError
    client = MagicMock(spec=BitgetClient)
    client.place_order.side_effect = BitgetAPIError("exchange down")
    risk = RiskEngine(str(RULES_PATH))
    runner = BitgetBotRunner(client, risk, simulated=False)
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001, price=50000)
    # Risk allowed, but exchange call failed
    assert res.allowed is True
    assert res.submitted is False
    assert "exchange down" in res.error


# ---------------------------------------------------------------------------
# Credentials from env
# ---------------------------------------------------------------------------
def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("BITGET_API_KEY", "k")
    monkeypatch.setenv("BITGET_API_SECRET", "s")
    monkeypatch.setenv("BITGET_PASSPHRASE", "p")
    monkeypatch.delenv("BITGET_DEMO", raising=False)
    creds = BitgetCredentials.from_env()
    assert creds.api_key == "k"
    assert creds.api_secret == "s"
    assert creds.passphrase == "p"
    assert creds.demo is True  # default
    monkeypatch.setenv("BITGET_DEMO", "0")
    assert BitgetCredentials.from_env().demo is False