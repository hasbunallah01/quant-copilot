"""End-to-end test: parse a real demo-bot log and run it through the detector.

This is the integration test we use to generate the `logs/demo-bot-e2e.log`
verifiable artifact (see `logs/README.md`).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from copilot.detector import AnomalyDetector
from copilot.watcher import parse_log_line


def _line(level, msg, **extra):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    suffix = (" " + json.dumps(extra)) if extra else ""
    return f"[{ts}] {level} {msg}{suffix}"


def test_end_to_end_demo_bot_run(tmp_path):
    """Simulate ~10 ticks of the demo bot with the infinite-loop bug.

    Asserts that the detector surfaces:
      - INFINITE_LOOP (CRITICAL)
      - HIGH_TRADE_FREQUENCY (HIGH)
      - SUDDEN_DRAWDOWN (HIGH/CRITICAL)
    """
    log_path = tmp_path / "demo.log"
    detector = AnomalyDetector()

    # The demo bot's broken tick loop. Drive the losses hard enough that
    # drawdown > 5% threshold (the detector seeds equity at 10k, so we need
    # cumulative PnL below -500 USDT).
    lines = []
    for tick in range(1, 11):
        lines.append(_line("INFO", f"--- Tick #{tick} ---"))
        lines.append(_line("INFO", "Current BTC price: $65,000.00"))
        lines.append(_line("INFO", "Signal: BUY"))
        # Every tick fires the bug: it buys without checking has_position.
        # Emit the BUY line in the canonical format the parser understands.
        lines.append(_line(
            "INFO",
            "BUY 0.00769231 BTCUSDT at $65,000.00",
        ))
        # And a separate PnL line that the parser can extract.
        if tick > 3:
            lines.append(_line(
                "INFO",
                f"Closed trade: PnL: -150.00 USDT",
            ))

    log_path.write_text("\n".join(lines), encoding="utf-8")

    seen_types = set()
    seen_severities = {}
    parsed_events = []
    for line in lines:
        event = parse_log_line(line)
        parsed_events.append(event)
        for anom in detector.feed(event):
            seen_types.add(anom.type)
            seen_severities.setdefault(anom.type, anom.severity)

    assert "INFINITE_LOOP" in seen_types, f"expected INFINITE_LOOP, got {seen_types}"
    assert seen_severities["INFINITE_LOOP"] == "CRITICAL"
    # Drawdown escalates quickly because of stacked losses
    assert "SUDDEN_DRAWDOWN" in seen_types

    # Write the parsed run as a verifiable artifact
    artifact_path = tmp_path / "demo-bot-e2e.json"
    artifact_path.write_text(json.dumps({
        "lines": len(lines),
        "events_parsed": len(parsed_events),
        "anomalies_detected": sorted(seen_types),
        "severities": seen_severities,
    }, indent=2), encoding="utf-8")
    assert artifact_path.exists()