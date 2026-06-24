"""
risk_engine.py - YAML-based risk policy engine.

Load risk rules from a YAML file and provide a `check()` method that
validates a proposed trade against those rules. This is the "pre-trade"
gatekeeper that can block bad orders before they reach the exchange.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class TradeRequest:
    """A proposed trade to be validated against risk rules."""

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float
    account_equity: float = 10000.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class CheckResult:
    """Result of a risk check."""

    allowed: bool
    reason: str = ""
    rule_violated: Optional[str] = None
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "rule_violated": self.rule_violated,
            "context": self.context,
        }


class RiskEngine:
    """
    Risk policy engine. Load rules from YAML, then call `check_trade()`
    for each proposed trade.

    Tracks a sliding window of recent trades to enforce rate limits.
    """

    def __init__(self, rules_path: str):
        self.rules_path = Path(rules_path)
        self.rules: dict = {}
        self._trade_history: deque = deque(maxlen=1000)
        self._daily_pnl: deque = deque(maxlen=10000)
        self._open_positions: dict[str, float] = {}  # symbol -> size in USDT
        self._peak_equity: float = 10000.0
        self._current_equity: float = 10000.0
        self._kill_switch_active: bool = False

        self.reload()

    def reload(self) -> None:
        """Reload rules from disk."""
        with open(self.rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}

    def update_equity(self, equity: float) -> None:
        """Update the current account equity (used for drawdown tracking)."""
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Auto-trigger kill switch
        if self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity
            if dd >= self.rules.get("kill_switch_drawdown", 1.0):
                self._kill_switch_active = True

    def reset_kill_switch(self) -> None:
        """Manually reset the kill switch (e.g. after manual review)."""
        self._kill_switch_active = False
        self._peak_equity = self._current_equity

    def record_trade(self, trade: TradeRequest, realized_pnl: float = 0.0) -> None:
        """Record that a trade was executed. Updates internal state."""
        self._trade_history.append((trade.timestamp, trade))
        self._daily_pnl.append((trade.timestamp, realized_pnl))
        notional = trade.quantity * trade.price
        if trade.side == "BUY":
            self._open_positions[trade.symbol] = (
                self._open_positions.get(trade.symbol, 0.0) + notional
            )
        elif trade.side == "SELL":
            self._open_positions[trade.symbol] = (
                self._open_positions.get(trade.symbol, 0.0) - notional
            )

    def check_trade(self, trade: TradeRequest) -> CheckResult:
        """
        Validate a proposed trade against the loaded risk rules.
        Returns a CheckResult. ALWAYS call this before placing an order.
        """
        # 0) Kill switch
        if self._kill_switch_active:
            return CheckResult(
                allowed=False,
                reason=(
                    "Kill switch active: drawdown exceeded threshold. "
                    "Manual review required to reset."
                ),
                rule_violated="kill_switch_drawdown",
                context={
                    "current_equity": self._current_equity,
                    "peak_equity": self._peak_equity,
                },
            )

        # 1) Symbol whitelist/blacklist
        blocked = self.rules.get("blocked_symbols", []) or []
        if trade.symbol in blocked:
            return CheckResult(
                allowed=False,
                reason=f"Symbol {trade.symbol} is on the blocklist",
                rule_violated="blocked_symbols",
                context={"symbol": trade.symbol, "blocked": blocked},
            )

        allowed_symbols = self.rules.get("allowed_symbols", []) or []
        if allowed_symbols and trade.symbol not in allowed_symbols:
            return CheckResult(
                allowed=False,
                reason=(
                    f"Symbol {trade.symbol} not in whitelist "
                    f"({len(allowed_symbols)} symbols allowed)"
                ),
                rule_violated="allowed_symbols",
                context={"symbol": trade.symbol, "allowed": allowed_symbols},
            )

        notional = trade.quantity * trade.price

        # 2) Max position size
        max_pos = float(self.rules.get("max_position_size", float("inf")))
        if notional > max_pos:
            return CheckResult(
                allowed=False,
                reason=(
                    f"Order notional {notional:.2f} USDT exceeds max position "
                    f"size of {max_pos:.2f} USDT"
                ),
                rule_violated="max_position_size",
                context={
                    "notional": notional,
                    "max_position_size": max_pos,
                },
            )

        # 3) Max order as % of equity
        max_pct = float(self.rules.get("max_order_pct_of_equity", 1.0))
        if trade.account_equity > 0:
            order_pct = notional / trade.account_equity
            if order_pct > max_pct:
                return CheckResult(
                    allowed=False,
                    reason=(
                        f"Order is {order_pct * 100:.1f}% of equity, exceeds "
                        f"max of {max_pct * 100:.1f}%"
                    ),
                    rule_violated="max_order_pct_of_equity",
                    context={
                        "order_pct": round(order_pct, 4),
                        "max_pct": max_pct,
                    },
                )

        # 4) Total exposure
        max_exp = float(self.rules.get("max_total_exposure", float("inf")))
        current_exp = sum(abs(v) for v in self._open_positions.values())
        if current_exp + notional > max_exp:
            return CheckResult(
                allowed=False,
                reason=(
                    f"Total exposure {current_exp + notional:.2f} USDT would "
                    f"exceed max of {max_exp:.2f} USDT"
                ),
                rule_violated="max_total_exposure",
                context={
                    "current_exposure": current_exp,
                    "new_exposure": current_exp + notional,
                    "max_exposure": max_exp,
                },
            )

        # 5) Trade rate (sliding window)
        now = trade.timestamp
        window = 60.0
        threshold = int(self.rules.get("max_trades_per_minute", 999))
        recent_count = sum(
            1 for ts, _ in self._trade_history if now - ts < window
        )
        if recent_count >= threshold:
            return CheckResult(
                allowed=False,
                reason=(
                    f"{recent_count} trades in last {int(window)}s, exceeds "
                    f"limit of {threshold}"
                ),
                rule_violated="max_trades_per_minute",
                context={
                    "trades_in_window": recent_count,
                    "window_seconds": window,
                    "threshold": threshold,
                },
            )

        # 6) Identical-trade rate (anti-loop)
        id_threshold = int(
            self.rules.get("max_identical_trades_per_minute", 999)
        )
        id_window = 60.0
        identical_count = sum(
            1
            for ts, t in self._trade_history
            if now - ts < id_window
            and t.symbol == trade.symbol
            and t.side == trade.side
        )
        if identical_count >= id_threshold:
            return CheckResult(
                allowed=False,
                reason=(
                    f"{identical_count} identical {trade.side} {trade.symbol} "
                    f"trades in last {int(id_window)}s (limit "
                    f"{id_threshold}) - likely infinite loop"
                ),
                rule_violated="max_identical_trades_per_minute",
                context={
                    "identical_count": identical_count,
                    "window_seconds": id_window,
                    "threshold": id_threshold,
                    "symbol": trade.symbol,
                    "side": trade.side,
                },
            )

        # 7) Daily loss limit
        max_daily = float(self.rules.get("max_daily_loss", float("inf")))
        day_pnl = sum(p for _, p in self._daily_pnl)
        if day_pnl <= -max_daily:
            return CheckResult(
                allowed=False,
                reason=(
                    f"Daily loss of {-day_pnl:.2f} USDT exceeds max of "
                    f"{max_daily:.2f} USDT - trading paused"
                ),
                rule_violated="max_daily_loss",
                context={
                    "daily_pnl": day_pnl,
                    "max_daily_loss": max_daily,
                },
            )

        return CheckResult(allowed=True)
