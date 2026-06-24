"""
demo_bot/bot.py - A deliberately-buggy trading bot for the demo.

This bot is meant to be RUN while looking at the Quant Copilot dashboard.
It contains an INTENTIONAL BUG: a missing position-state check that
causes it to enter the same trade in an infinite loop.

When you start the dashboard, then start this bot, you will see:
  1. The bot starts and tries to open a position
  2. The position opens
  3. The signal fires AGAIN on the next tick - bot buys again
  4. And again. And again. And again.
  5. Quant Copilot detects the pattern within seconds
  6. AI doctor explains the bug
  7. Risk engine blocks the 3rd identical trade
  8. Kill switch triggers when drawdown exceeds 10%

To run:
  Terminal 1:  python -m copilot.dashboard
  Terminal 2:  python demo_bot/bot.py

Prices come from CoinGecko's free public API (no key required).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "demo.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Bitget testnet public price endpoint, with CoinGecko fallback
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
}

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
def log(level: str, message: str, **extra) -> None:
    """Write a structured log line to demo.log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {level} {message}"
    if extra:
        line += " " + json.dumps(extra)
    line += "\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    # Also print to stdout so you can watch in terminal
    sys.stdout.write(line)
    sys.stdout.flush()


# -----------------------------------------------------------------------------
# Price feed (CoinGecko - free, no key required)
# -----------------------------------------------------------------------------
_OFFLINE_PRICES = {
    "BTC": 65000.0,
    "ETH": 3200.0,
    "SOL": 145.0,
    "BNB": 580.0,
}


def fetch_price(symbol: str) -> Optional[float]:
    """
    Fetch current price in USD from CoinGecko.
    Falls back to a deterministic offline price on rate limit / error
    so the demo always works (CoinGecko's free tier is heavily rate-limited
    from cloud IPs).
    """
    cg_id = COINGECKO_IDS.get(symbol.upper())
    if not cg_id:
        return _OFFLINE_PRICES.get(symbol.upper())
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=3,
        )
        r.raise_for_status()
        data = r.json()
        return float(data[cg_id]["usd"])
    except Exception as e:
        # Graceful fallback for the demo
        log("WARN", f"Price fetch failed for {symbol}, using offline fallback: {e}")
        return _OFFLINE_PRICES.get(symbol.upper())


# -----------------------------------------------------------------------------
# THE BUGGY TRADING BOT
# -----------------------------------------------------------------------------
class BuggyBot:
    """
    A toy trading bot with an intentional infinite-loop bug.

    THE BUG (intentionally present for the demo):
        In `on_tick()`, the entry condition checks the signal but does NOT
        check whether we already have an open position in that symbol.
        This means every tick where the signal is True, we keep buying
        more of the same coin - doubling exposure every cycle.
    """

    def __init__(self, symbol: str = "BTC", trade_size_usd: float = 500.0):
        self.symbol = symbol
        self.trade_size_usd = trade_size_usd
        self.has_position = False        # <-- THE BUG IS HERE
        self.position_size = 0.0
        self.equity = 10000.0
        self.peak_equity = 10000.0
        self.tick_count = 0
        self.entry_price: Optional[float] = None
        self.running = True
        self.tick_interval = 3.0          # seconds between ticks

    def start(self) -> None:
        log("INFO", f"Bot starting on {self.symbol} (demo mode with intentional bug)")
        log("INFO", f"Trade size: {self.trade_size_usd} USDT, tick interval: {self.tick_interval}s")
        log("INFO", f"NOTE: This bot contains an intentional infinite-loop bug for the demo")

        try:
            while self.running and self.tick_count < 200:
                self.tick()
                time.sleep(self.tick_interval)
        except KeyboardInterrupt:
            log("INFO", "Bot stopped by user")
        finally:
            log("INFO", f"Bot finished. Total ticks: {self.tick_count}, Final equity: {self.equity:.2f} USDT")

    def tick(self) -> None:
        """One iteration of the main loop."""
        self.tick_count += 1
        log("INFO", f"--- Tick #{self.tick_count} ---")

        price = fetch_price(self.symbol)
        if price is None:
            log("WARN", "Could not fetch price, skipping tick")
            return

        log("INFO", f"Current {self.symbol} price: ${price:,.2f}")

        # === SIGNAL ===
        # In a real bot this would be an indicator (RSI, MA cross, etc.)
        # For the demo we use a simple mock: signal = True if price is above $60k
        signal = price > 60000 if self.symbol == "BTC" else price > 3000

        log("INFO", f"Signal: {'BUY' if signal else 'HOLD'}")

        # === THE BUG ===
        # The bot checks the signal but does NOT check self.has_position
        # before placing the trade. This is the infinite-loop bug.
        if signal:
            self.buy(price)  # <-- Missing: `and not self.has_position`
        # else: ... (no close logic in this demo)

        # Update equity tracking
        if self.peak_equity < self.equity:
            self.peak_equity = self.equity
        dd_pct = ((self.peak_equity - self.equity) / self.peak_equity) * 100
        if dd_pct > 0:
            log("INFO", f"Equity: {self.equity:.2f} USDT (drawdown: {dd_pct:.2f}%)")

        # Halt on extreme drawdown
        if dd_pct >= 20.0:
            log("ERROR", f"Drawdown {dd_pct:.2f}% exceeds 20% - halting")
            self.running = False

    def buy(self, price: float) -> None:
        """Execute a buy order (simulated, logged only)."""
        # === THE FIX WOULD BE ===
        # if self.has_position:
        #     log("INFO", "Already in position, skipping")
        #     return
        qty = self.trade_size_usd / price
        self.has_position = True
        self.position_size += qty
        # Simulate a small negative slippage / fee impact
        self.equity -= self.trade_size_usd * 0.001  # 0.1% fee
        log("INFO", f"BUY {qty:.6f} {self.symbol} at ${price:,.2f} (notional: {self.trade_size_usd} USDT)")
        # For drawdown demo, deduct a tiny loss every buy
        self.equity -= 5.0
        if self.entry_price is None:
            self.entry_price = price


def main() -> None:
    print("\n" + "=" * 60)
    print("  Quant Copilot Demo Bot (with intentional bug)")
    print("=" * 60)
    print(f"\n  Log file: {LOG_FILE}")
    print(f"  Open http://localhost:8000 in your browser to see the dashboard")
    print(f"  Press Ctrl+C to stop\n")

    # Truncate the log file for a fresh demo
    if "--keep-logs" not in sys.argv:
        LOG_FILE.write_text("")

    symbol = os.environ.get("DEMO_SYMBOL", "BTC")
    bot = BuggyBot(symbol=symbol)
    bot.start()


if __name__ == "__main__":
    main()
