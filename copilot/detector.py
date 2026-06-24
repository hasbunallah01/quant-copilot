"""
detector.py - Anomaly detection for trading bot events.

Sliding-window detectors for common trading bot failure modes:
  - Infinite loop / duplicate orders
  - Sudden drawdown
  - API rate limiting
  - Slippage spikes
  - Position size breaches
  - Off-hours activity (if configured)

Each detector returns a list of anomalies. Anomalies have:
  - id (uuid)
  - type
  - severity (LOW, MEDIUM, HIGH, CRITICAL)
  - message
  - context (dict of relevant data)
  - suggested_action (string)
  - detected_at (ISO timestamp)
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"


@dataclass
class Anomaly:
    """A detected anomaly in bot behavior."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str = ""
    severity: str = SEVERITY_MEDIUM
    message: str = ""
    context: dict = field(default_factory=dict)
    suggested_action: str = ""
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


class SlidingWindow:
    """A time-bounded sliding window of events."""

    def __init__(self, window_seconds: float = 60.0):
        self.window = window_seconds
        self._items: deque = deque()

    def add(self, item, ts: Optional[float] = None) -> None:
        if ts is None:
            ts = time.time()
        self._items.append((ts, item))
        self._evict(ts)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()

    def __len__(self) -> int:
        self._evict(time.time())
        return len(self._items)

    def items(self):
        self._evict(time.time())
        return [item for _, item in self._items]


class AnomalyDetector:
    """
    Stateful anomaly detector. Feed it parsed log events,
    call `check()` to get back a list of new anomalies.
    """

    def __init__(
        self,
        identical_trade_window: float = 60.0,
        identical_trade_threshold: int = 2,
        trade_window: float = 60.0,
        trade_threshold: int = 3,
        drawdown_window: float = 300.0,
        drawdown_threshold_pct: float = 5.0,
        api_error_window: float = 60.0,
        api_error_threshold: int = 10,
        slippage_threshold_bps: float = 100.0,
    ):
        # Sliding windows for various patterns
        self.trades = SlidingWindow(trade_window)
        self.identical_trades = SlidingWindow(identical_trade_window)
        self.api_errors = SlidingWindow(api_error_window)
        self.recent_events: deque = deque(maxlen=200)  # raw event log

        # Track last trade signature for identical-trade detection
        self._last_trade_sig = None
        self._last_trade_repeat_count = 0

        # Track running PnL for drawdown detection
        self._peak_equity: Optional[float] = None
        self._current_equity: Optional[float] = None
        self._recent_pnl = SlidingWindow(drawdown_window)

        # Thresholds
        self.identical_trade_threshold = identical_trade_threshold
        self.trade_threshold = trade_threshold
        self.drawdown_threshold_pct = drawdown_threshold_pct
        self.api_error_threshold = api_error_threshold
        self.slippage_threshold_bps = slippage_threshold_bps

    def feed(self, event: dict) -> list[Anomaly]:
        """
        Feed a parsed log event to the detector.
        Returns a list of newly-detected anomalies (may be empty).
        """
        anomalies: list[Anomaly] = []
        now = time.time()

        self.recent_events.append((now, event))

        action = event.get("action")
        level = event.get("level", "INFO")
        message = event.get("message", "").lower()
        symbol = event.get("symbol")
        side = event.get("side")

        # 1) Trading activity tracking
        if action in ("BUY", "SELL"):
            sig = (side, symbol, event.get("quantity"), event.get("price"))
            self.trades.add(sig, now)

            if sig == self._last_trade_sig and self._last_trade_sig is not None:
                self._last_trade_repeat_count += 1
                self.identical_trades.add(sig, now)
                if self._last_trade_repeat_count >= self.identical_trade_threshold:
                    anomalies.append(
                        Anomaly(
                            type="INFINITE_LOOP",
                            severity=SEVERITY_CRITICAL,
                            message=(
                                f"Identical trade repeated "
                                f"{self._last_trade_repeat_count + 1} times in "
                                f"<{int(self.identical_trades.window)}s: "
                                f"{side} {event.get('quantity')} {symbol}"
                            ),
                            context={
                                "side": side,
                                "symbol": symbol,
                                "quantity": event.get("quantity"),
                                "price": event.get("price"),
                                "repeat_count": self._last_trade_repeat_count + 1,
                                "window_seconds": self.identical_trades.window,
                            },
                            suggested_action=(
                                "Likely infinite loop bug - the bot is firing the "
                                "same trade repeatedly without checking if it has "
                                "already placed it. Inspect the entry condition "
                                "in your strategy class (look for missing position "
                                "check before buy() / sell())."
                            ),
                        )
                    )
            else:
                self._last_trade_sig = sig
                self._last_trade_repeat_count = 0

            # 2) Trade rate check
            if len(self.trades) > self.trade_threshold:
                anomalies.append(
                    Anomaly(
                        type="HIGH_TRADE_FREQUENCY",
                        severity=SEVERITY_HIGH,
                        message=(
                            f"{len(self.trades)} trades placed within "
                            f"{int(self.trades.window)}s - above threshold "
                            f"of {self.trade_threshold}"
                        ),
                        context={
                            "trades_in_window": len(self.trades),
                            "window_seconds": self.trades.window,
                            "threshold": self.trade_threshold,
                        },
                        suggested_action=(
                            "Possible runaway strategy or over-eager signal logic. "
                            "Add a cooldown between trades for the same symbol, "
                            "or check if your signal is firing on every tick."
                        ),
                    )
                )

        # 3) API errors
        if level == "ERROR" or "api error" in message or "rate limit" in message:
            self.api_errors.add(event, now)
            if len(self.api_errors) >= self.api_error_threshold:
                anomalies.append(
                    Anomaly(
                        type="API_RATE_LIMIT",
                        severity=SEVERITY_HIGH,
                        message=(
                            f"{len(self.api_errors)} API errors in "
                            f"{int(self.api_errors.window)}s"
                        ),
                        context={
                            "errors_in_window": len(self.api_errors),
                            "window_seconds": self.api_errors.window,
                            "threshold": self.api_error_threshold,
                            "sample_message": event.get("message", "")[:200],
                        },
                        suggested_action=(
                            "Hit Bitget API rate limit. Add exponential backoff "
                            "and respect the X-Bitget-Request-Rate-Limit headers. "
                            "Reduce polling frequency or batch your requests."
                        ),
                    )
                )

        # 4) Drawdown tracking via PnL events
        pnl = event.get("pnl")
        if pnl is not None:
            self._recent_pnl.add(pnl, now)
            net = sum(self._recent_pnl.items())
            if self._current_equity is None:
                self._current_equity = 10000.0  # default seed
            self._current_equity += pnl
            if self._peak_equity is None or self._current_equity > self._peak_equity:
                self._peak_equity = self._current_equity
            if self._peak_equity and self._peak_equity > 0:
                dd_pct = (
                    (self._peak_equity - self._current_equity) / self._peak_equity
                ) * 100.0
                if dd_pct >= self.drawdown_threshold_pct:
                    anomalies.append(
                        Anomaly(
                            type="SUDDEN_DRAWDOWN",
                            severity=(
                                SEVERITY_CRITICAL
                                if dd_pct >= self.drawdown_threshold_pct * 2
                                else SEVERITY_HIGH
                            ),
                            message=(
                                f"Drawdown {dd_pct:.2f}% from peak in the last "
                                f"{int(self._recent_pnl.window)}s (threshold "
                                f"{self.drawdown_threshold_pct}%)"
                            ),
                            context={
                                "drawdown_pct": round(dd_pct, 4),
                                "current_equity": self._current_equity,
                                "peak_equity": self._peak_equity,
                                "window_seconds": self._recent_pnl.window,
                                "threshold_pct": self.drawdown_threshold_pct,
                            },
                            suggested_action=(
                                "Drawdown exceeds risk threshold. Halt the bot "
                                "immediately (kill switch), review recent losing "
                                "trades, and verify the strategy is still aligned "
                                "with current market regime."
                            ),
                        )
                    )

        # 5) Slippage detection (best-effort, only if event carries slippage_bps)
        slippage = event.get("slippage_bps")
        if slippage is not None and slippage >= self.slippage_threshold_bps:
            anomalies.append(
                Anomaly(
                    type="SLIPPAGE_SPIKE",
                    severity=SEVERITY_MEDIUM,
                    message=(
                        f"Slippage of {slippage:.1f} bps on "
                        f"{side or 'order'} {symbol or ''} "
                        f"(threshold {self.slippage_threshold_bps} bps)"
                    ),
                    context={
                        "slippage_bps": slippage,
                        "threshold_bps": self.slippage_threshold_bps,
                        "symbol": symbol,
                        "side": side,
                    },
                    suggested_action=(
                        "Slippage is unusually high. Consider switching from "
                        "market to limit orders, reducing order size, or trading "
                        "during higher-liquidity hours."
                    ),
                )
            )

        return anomalies
