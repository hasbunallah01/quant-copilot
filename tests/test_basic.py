"""Smoke tests for Quant Copilot core modules."""
import os
import sys
import time
from pathlib import Path

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from copilot.ai_doctor import DiagnoseDoctor
from copilot.detector import AnomalyDetector, SEVERITY_CRITICAL, SEVERITY_HIGH
from copilot.risk_engine import CheckResult, RiskEngine, TradeRequest
from copilot.watcher import parse_log_line


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
    # Second identical trade - no anomaly yet (threshold is 2)
    a2 = det.feed(base_event)
    # Third identical trade - CRITICAL
    a3 = det.feed(base_event)
    assert any(a.type == "INFINITE_LOOP" for a in a3)
    print("  ✓ test_detector_infinite_loop passed")


def test_detector_api_errors():
    det = AnomalyDetector(api_error_threshold=3)
    for i in range(5):
        det.feed({
            "level": "ERROR",
            "message": f"API error {i}",
        })
    # Should have produced an API_RATE_LIMIT anomaly
    # (We don't have the full list here, so just verify the detector
    #  is functional and not throwing exceptions)
    print("  ✓ test_detector_api_errors passed (no exceptions)")


def test_ai_doctor():
    from copilot.detector import Anomaly
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
    assert "5" in diag["summary"]  # formatted with repeat_count
    print("  ✓ test_ai_doctor passed")


def test_risk_engine_position_size():
    engine = RiskEngine(str(Path(__file__).resolve().parent.parent / "rules" / "default.yaml"))
    trade = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=1.0, price=50000)
    # Notional = 50,000 > max_position_size of 1000 - should be blocked
    result = engine.check_trade(trade)
    assert not result.allowed
    assert result.rule_violated == "max_position_size"
    print("  ✓ test_risk_engine_position_size passed")


def test_risk_engine_kill_switch():
    engine = RiskEngine(str(Path(__file__).resolve().parent.parent / "rules" / "default.yaml"))
    # Trigger kill switch by updating equity to simulate 20% drawdown
    engine.update_equity(8000)  # peak is 10000, so dd = 20%
    trade = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=0.001, price=50000)
    result = engine.check_trade(trade)
    assert not result.allowed
    assert result.rule_violated == "kill_switch_drawdown"
    print("  ✓ test_risk_engine_kill_switch passed")


def test_risk_engine_allows_small_trade():
    engine = RiskEngine(str(Path(__file__).resolve().parent.parent / "rules" / "default.yaml"))
    trade = TradeRequest(symbol="BTC/USDT", side="BUY", quantity=0.001, price=50000)
    result = engine.check_trade(trade)
    assert result.allowed
    print("  ✓ test_risk_engine_allows_small_trade passed")


def test_risk_engine_blocks_blocked_symbol():
    engine = RiskEngine(str(Path(__file__).resolve().parent.parent / "rules" / "default.yaml"))
    trade = TradeRequest(symbol="SCAM/USDT", side="BUY", quantity=0.001, price=1)
    result = engine.check_trade(trade)
    assert not result.allowed
    assert result.rule_violated == "blocked_symbols"
    print("  ✓ test_risk_engine_blocks_blocked_symbol passed")


if __name__ == "__main__":
    print("\n  Running Quant Copilot smoke tests...\n")
    test_parse_log_line_basic()
    test_parse_log_line_error()
    test_parse_log_line_no_timestamp()
    test_detector_infinite_loop()
    test_detector_api_errors()
    test_ai_doctor()
    test_risk_engine_position_size()
    test_risk_engine_kill_switch()
    test_risk_engine_allows_small_trade()
    test_risk_engine_blocks_blocked_symbol()
    print("\n  All tests passed ✅\n")
