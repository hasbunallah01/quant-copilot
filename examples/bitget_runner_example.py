"""
bitget_runner_example.py - End-to-end usage of BitgetBotRunner.

This example is fully offline (uses a mocked BitgetClient) so it runs
without API keys. To run against real Bitget demo trading:

    export BITGET_API_KEY=...
    export BITGET_API_SECRET=...
    export BITGET_PASSPHRASE=...
    python examples/bitget_runner_example.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from copilot.exchanges import BitgetBotRunner, BitgetClient
from copilot.exchanges.bitget_client import BitgetAPIError
from copilot.risk_engine import RiskEngine


def main() -> int:
    print("\n  Quant Copilot - BitgetBotRunner example\n")
    print("  Step 1: load risk policy from rules/default.yaml")
    risk = RiskEngine("rules/default.yaml")
    print(f"    max_position_size     = {risk.rules['max_position_size']} USDT")
    print(f"    max_trades_per_minute = {risk.rules['max_trades_per_minute']}")
    print(f"    kill_switch_drawdown  = {risk.rules['kill_switch_drawdown'] * 100:.0f}%\n")

    print("  Step 2: build a mocked Bitget client (offline demo)")
    fake_client = MagicMock(spec=BitgetClient)
    fake_client.place_order.return_value = {
        "orderId": "112233445566778899",
        "clientOid": "qc-demo-1",
    }
    print("    (in production: BitgetClient(credentials=BitgetCredentials.from_env()))\n")

    runner = BitgetBotRunner(fake_client, risk, simulated=False)

    print("  Step 3: try a small order (should be ALLOWED + submitted)")
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001,
                       price=50000, order_type="LIMIT")
    print(f"    allowed    = {res.allowed}")
    print(f"    submitted  = {res.submitted}")
    print(f"    order_resp = {res.order_response}\n")

    print("  Step 4: try an oversized order (should be BLOCKED, no API call)")
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=1.0, price=50000)
    print(f"    allowed      = {res.allowed}")
    print(f"    rule_violated = {res.rule_violated}")
    print(f"    reason       = {res.reason}\n")

    print("  Step 5: trigger the kill switch and watch everything block")
    risk.update_equity(8000)  # 20% drawdown
    res = runner.place(symbol="BTCUSDT", side="BUY", qty=0.001, price=50000)
    print(f"    allowed      = {res.allowed}")
    print(f"    rule_violated = {res.rule_violated}")
    print(f"    reason       = {res.reason}\n")

    print("  Done. The same runner talks to live Bitget when you swap the\n"
          "  mocked client for BitgetClient(credentials=BitgetCredentials.from_env()).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())