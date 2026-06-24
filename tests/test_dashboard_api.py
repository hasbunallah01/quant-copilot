"""End-to-end tests for the FastAPI dashboard.

Uses FastAPI's `TestClient` (no live socket) so the suite runs offline.
Covers:
  - GET /  -> HTML shell
  - GET /api/status, /api/logs, /api/anomalies
  - POST /api/check-trade (allowed vs blocked)
  - POST /api/reset-kill-switch
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from copilot.dashboard import app, STATE  # noqa: E402


@pytest.fixture
def client():
    # Avoid starting the actual LogWatcher against a real file in the test
    # environment; we only need the HTTP layer.
    STATE.bot_status = "running"
    STATE.log_buffer.clear()
    STATE.anomalies.clear()
    return TestClient(app)


def test_root_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Quant Copilot" in r.text
    assert "<title>" in r.text


def test_status_endpoint(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("idle", "running", "halted")
    assert "kill_switch_active" in body
    assert "current_equity" in body


def test_logs_endpoint(client):
    r = client.get("/api/logs")
    assert r.status_code == 200
    assert "events" in r.json()


def test_anomalies_endpoint(client):
    r = client.get("/api/anomalies")
    assert r.status_code == 200
    assert "anomalies" in r.json()


def test_check_trade_allowed(client):
    r = client.post("/api/check-trade", json={
        "symbol": "BTC/USDT",
        "side": "BUY",
        "quantity": 0.001,
        "price": 50000,
        "account_equity": 10000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True


def test_check_trade_blocked_position_size(client):
    r = client.post("/api/check-trade", json={
        "symbol": "BTC/USDT",
        "side": "BUY",
        "quantity": 1.0,           # 50,000 USDT notional > 1000 cap
        "price": 50000,
        "account_equity": 10000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is False
    assert body["rule_violated"] == "max_position_size"


def test_check_trade_blocked_blacklist(client):
    r = client.post("/api/check-trade", json={
        "symbol": "SCAM/USDT",
        "side": "BUY",
        "quantity": 0.001,
        "price": 1,
        "account_equity": 10000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is False
    assert body["rule_violated"] == "blocked_symbols"


def test_reset_kill_switch(client):
    # Force the kill switch on via the risk engine
    STATE.risk_engine.update_equity(8000)
    assert STATE.risk_engine._kill_switch_active is True

    r = client.post("/api/reset-kill-switch")
    assert r.status_code == 200
    assert r.json()["kill_switch_active"] is False
    assert STATE.risk_engine._kill_switch_active is False


def test_check_trade_blocks_after_kill_switch(client):
    # Force the kill switch by setting equity to a level that trips the
    # 10% drawdown threshold from the seeded 10k peak. We bypass any state
    # leakage from prior tests by resetting the risk engine first.
    STATE.risk_engine.reset_kill_switch()
    STATE.risk_engine._peak_equity = 10000.0
    STATE.risk_engine.update_equity(8000)  # 20% drawdown -> kill switch
    r = client.post("/api/check-trade", json={
        "symbol": "BTC/USDT",
        "side": "BUY",
        "quantity": 0.001,
        "price": 50000,
        "account_equity": 10000,
    })
    body = r.json()
    assert body["allowed"] is False
    assert body["rule_violated"] == "kill_switch_drawdown"
    # Reset for any later tests
    client.post("/api/reset-kill-switch")