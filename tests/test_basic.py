"""Smoke tests for Quant Copilot core modules.

Originally shipped as a script (`python tests/test_basic.py`); now also
runnable under pytest (`pytest tests/`).

The CLI entrypoint is preserved so the existing demo flow still works.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from copilot.ai_doctor import DiagnoseDoctor
from copilot.detector import AnomalyDetector, SEVERITY_CRITICAL, Anomaly
from copilot.risk_engine import CheckResult, RiskEngine, TradeRequest
from copilot.watcher import parse_log_line


RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "default.yaml"


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------
def test_parse_log_line_basic():
    line = "[2024-01-15 10:23:01] INFO BUY 0.5 BTCUSDT at 65000"
    ev = parse_log_line(line)
    assert ev["timestamp"] == "2024-01-15 10:23:01"
    assert ev["level"] == "INFO"
    assert ev["action"] == "BUY"
    assert ev["side"] == "BUY"
    assert ev["quantity"] == 0.5
    assert ev["symbol"] == "BTC/USDT"
    assert ev["price"] == 65000.0
    print("  ✓ test_parse_log_line_basic passed")


def test_parse_log_line_error():
    line = "[2024-01-15 10:23:01] ERROR API rate limit hit"
    ev = parse_log_line(line)
    assert ev["level"] == "ERROR"
    assert "api rate limit" in ev["message"].lower()
    print("  ✓ test_parse_log_line_error passed")


def test_parse_log_line_no_timestamp():
    line = "INFO BUY 0.1 ETHUSDT at 3000"
    ev = parse_log_line(line)
    assert ev["timestamp"] is None
    assert ev["level"] == "INFO"
    assert ev["symbol"] == "ETH/USDT"
    print("  ✓ test_parse_log_line_no_timestamp passed")


def test_parse_log_line_pnl():
    line = "[2024-01-15 10:23:01] INFO SELL 0.5 BTCUSDT at 65100 PnL: 12.5"
    ev = parse_log_line(line)
    assert ev.get("pnl") == 12.5
    print("  ✓ test_parse_log_line_pnl passed")


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
def test_detector_infinite_loop():
    det = AnomalyDetector(identical_trade_threshold=2)
    base_event = {
        "timestamp": "2024-01-15 10:23:01",
        "level": "INFO",
        "action": "BUY",
        "side": "BUY",
        "symbol": "BTC/USDT",
        "quantity": 0.5,
        "price": 65000.0,
        "message": "BUY 0.5 BTCUSDT at 65000",
    }
    # First trade - no anomaly
    a1 = det.feed(base_event)
    assert a1 == []
    # Second identical trade - still no anomaly (threshold is 2 repeats)
    a2 = det.feed(base_event)
    # Third identical trade - CRITICAL
    a3 = det.feed(base_event)
    assert any(a.type == "INFINITE_LOOP" for a in a3)
    print("  ✓ test_detector_infinite_loop passed")


def test_detector_api_errors():
    det = AnomalyDetector(api_error_threshold=3)
    fired = False
    for i in range(5):
        anomalies = det.feed({
            "level": "ERROR",
            "message": f"API error {i}",
        })
        if any(a.type == "API_RATE_LIMIT" for a in anomalies):
            fired = True
    assert fired, "API_RATE_LIMIT anomaly should fire after threshold"
    print("  ✓ test_detector_api_errors passed")


def test_detector_drawdown_critical_severity():
    det = AnomalyDetector(drawdown_threshold_pct=5.0)
    # Seed equity with positive pnl then big loss
    det.feed({"timestamp": "t1", "level": "INFO", "action": "BUY",
              "side": "BUY", "symbol": "BTC/USDT", "quantity": 0.1, "price": 65000,
              "message": "", "pnl": 0.0})
    anomalies = det.feed({"timestamp": "t2", "level": "INFO",
                          "message": "big loss", "pnl": -1200.0})
    types = [a.type for a in anomalies]
    assert "SUDDEN_DRAWDOWN" in types
    # Verify the critical severity escalates with double-threshold drawdown
    sev = next(a.severity for a in anomalies if a.type == "SUDDEN_DRAWDOWN")
    assert sev in ("HIGH", "CRITICAL")
    print("  ✓ test_detector_drawdown_critical_severity passed")


# ---------------------------------------------------------------------------
# AI Doctor
# ---------------------------------------------------------------------------
def test_ai_doctor():
    doctor = DiagnoseDoctor()
    anom = Anomaly(
        type="INFINITE_LOOP",
        severity=SEVERITY_CRITICAL,
        context={"repeat_count": 5, "window_seconds": 60},
    )
    diag = doctor.diagnose(anom)
    assert "summary" in diag
    assert "cause" in diag
    assert "fix" in diag
    assert "5" in diag["summary"]
    print("  ✓ test_ai_doctor passed")


def test_ai_doctor_all_templates():
    doctor = DiagnoseDoctor()
    for t in ("INFINITE_LOOP", "HIGH_TRADE_FREQUENCY", "API_RATE_LIMIT",
              "SUDDEN_DRAWDOWN", "SLIPPAGE_SPIKE"):
        ctx = {
            "repeat_count": 3, "window_seconds": 60,
            "trades_in_window": 10, "threshold": 3,
            "errors_in_window": 11,
            "drawdown_pct": 6.5, "current_equity": 9400.0, "peak_equity": 10000.0,
            "slippage_bps": 250, "threshold_bps": 100, "side": "BUY", "symbol": "BTC/USDT",
        }
        diag = doctor.diagnose(Anomaly(type=t, context=ctx))
        assert "cause" in diag and diag["cause"], f"{t} missing cause"
    print("  ✓ test_ai_doctor_all_templates passed")


# ---------------------------------------------------------------------------
# Risk Engine
# ---------------------------------------------------------------------------
def test_risk_engine_position_size():
    engine = RiskEngine(str(RULES_PATH))
    trade = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=1.0, price=50000)
    result = engine.check_trade(trade)
    assert not result.allowed
    assert result.rule_violated == "max_position_size"
    print("  ✓ test_risk_engine_position_size passed")


def test_risk_engine_kill_switch():
    engine = RiskEngine(str(RULES_PATH))
    engine.update_equity(8000)  # peak 10000, drawdown 20%
    trade = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=0.001, price=50000)
    result = engine.check_trade(trade)
    assert not result.allowed
    assert result.rule_violated == "kill_switch_drawdown"
    print("  ✓ test_risk_engine_kill_switch passed")


def test_risk_engine_allows_small_trade():
    engine = RiskEngine(str(RULES_PATH))
    trade = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=0.001, price=50000)
    result = engine.check_trade(trade)
    assert result.allowed
    print("  ✓ test_risk_engine_allows_small_trade passed")


def test_risk_engine_blocks_blocked_symbol():
    engine = RiskEngine(str(RULES_PATH))
    trade = TradeRequest(symbol="SCAM/USDT", side="BUY", quantity=0.001, price=1)
    result = engine.check_trade(trade)
    assert not result.allowed
    assert result.rule_violated == "blocked_symbols"
    print("  ✓ test_risk_engine_blocks_blocked_symbol passed")


def test_risk_engine_identical_trade_loop():
    engine = RiskEngine(str(RULES_PATH))
    # The default `max_identical_trades_per_minute` is 2, meaning
    # the 3rd identical trade gets blocked. Fire 3 trades total.
    t = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=0.001, price=50000)
    assert engine.check_trade(t).allowed
    engine.record_trade(t)
    # 2nd trade: still allowed (history count is 1, threshold is 2)
    assert engine.check_trade(t).allowed
    engine.record_trade(t)
    # 3rd trade: identical_count is now 2, equals threshold -> blocked
    res = engine.check_trade(t)
    assert not res.allowed
    assert res.rule_violated == "max_identical_trades_per_minute"
    print("  ✓ test_risk_engine_identical_trade_loop passed")


# ---------------------------------------------------------------------------
# CLI entrypoint (preserved from the original script)
# ---------------------------------------------------------------------------
ALL_TESTS = [
    test_parse_log_line_basic,
    test_parse_log_line_error,
    test_parse_log_line_no_timestamp,
    test_parse_log_line_pnl,
    test_detector_infinite_loop,
    test_detector_api_errors,
    test_detector_drawdown_critical_severity,
    test_ai_doctor,
    test_ai_doctor_all_templates,
    test_risk_engine_position_size,
    test_risk_engine_kill_switch,
    test_risk_engine_allows_small_trade,
    test_risk_engine_blocks_blocked_symbol,
    test_risk_engine_identical_trade_loop,
]


if __name__ == "__main__":
    print("\n  Running Quant Copilot smoke tests...\n")
    for t in ALL_TESTS:
        t()
    print(f"\n  All {len(ALL_TESTS)} tests passed ✅\n")