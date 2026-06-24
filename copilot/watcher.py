"""
watcher.py - Log file watcher for trading bots.

Tails one or more log files and emits new lines as structured events.
Uses polling (cross-platform, no watchdog dependency required at runtime).
"""
from __future__ import annotations

import os
import time
import threading
from pathlib import Path
from typing import Callable, Optional


class LogWatcher:
    """
    Tails a log file and calls `on_line` for each new line.

    Robust against:
      - File rotation (detects inode change)
      - File truncation (detects size shrink)
      - Missing file (waits for it to appear)
      - Slow producers (no busy-loop)
    """

    def __init__(
        self,
        path: str,
        on_line: Callable[[str], None],
        poll_interval: float = 0.2,
        from_start: bool = False,
    ):
        self.path = Path(path)
        self.on_line = on_line
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._inode: Optional[int] = None
        self._position: int = 0
        self._from_start = from_start

    def start(self) -> None:
        """Start watching in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"LogWatcher({self.path.name})", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the watcher."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        """Main loop. Polls the file for new content."""
        path_str = str(self.path)
        # Wait for file to exist
        while not self._stop_event.is_set() and not os.path.exists(path_str):
            time.sleep(self.poll_interval)

        if self._stop_event.is_set():
            return

        try:
            self._inode = os.stat(path_str).st_ino
            # Seek to end unless we want to read from the start
            self._position = 0 if self._from_start else os.path.getsize(path_str)
        except OSError:
            return

        buffer = ""

        while not self._stop_event.is_set():
            try:
                # Detect rotation/truncation
                current_inode = os.stat(path_str).st_ino
                current_size = os.stat(path_str).st_size

                if current_inode != self._inode:
                    # File rotated - reopen from start
                    self._inode = current_inode
                    self._position = 0

                if current_size < self._position:
                    # Truncated - reset
                    self._position = 0

                if current_size > self._position:
                    with open(path_str, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._position)
                        chunk = f.read(current_size - self._position)
                        self._position = current_size
                        buffer += chunk

                        # Process complete lines, keep partial line in buffer
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.rstrip("\r")
                            if line.strip():
                                try:
                                    self.on_line(line)
                                except Exception:
                                    # Never let a consumer crash kill the watcher
                                    pass
            except FileNotFoundError:
                # File disappeared (rotation in progress) - wait for it
                self._inode = None
                time.sleep(self.poll_interval)
                continue
            except OSError:
                time.sleep(self.poll_interval)
                continue

            time.sleep(self.poll_interval)


def parse_log_line(line: str) -> dict:
    """
    Parse a log line into a structured event.

    Tolerant parser - returns a dict with at minimum {"raw": line}.
    Recognized formats:
      [2024-01-15 10:23:01] LEVEL message
      2024-01-15T10:23:01.123Z LEVEL message
      LEVEL message  (no timestamp)
    Also recognizes action keywords like BUY/SELL/CLOSE/ERROR.
    """
    import re

    result = {
        "raw": line,
        "timestamp": None,
        "level": None,
        "message": line,
        "action": None,
        "symbol": None,
        "side": None,
        "quantity": None,
        "price": None,
    }

    # Strip optional leading timestamp
    ts_match = re.match(
        r"^\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*", line
    )
    if not ts_match:
        ts_match = re.match(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\s*", line
        )
    if ts_match:
        result["timestamp"] = ts_match.group(1)
        remainder = line[ts_match.end():]
    else:
        remainder = line

    # Strip optional level tag
    lvl_match = re.match(r"^(INFO|WARN|WARNING|ERROR|DEBUG|CRITICAL)\s+", remainder)
    if lvl_match:
        result["level"] = lvl_match.group(1)
        if result["level"] == "WARNING":
            result["level"] = "WARN"
        remainder = remainder[lvl_match.end():]
    else:
        result["level"] = "INFO"

    result["message"] = remainder.strip()

    # Detect trading action
    action_match = re.match(
        r"^(BUY|SELL|CLOSE|OPEN|CANCEL|ERROR)\s+", remainder, re.IGNORECASE
    )
    if action_match:
        result["action"] = action_match.group(1).upper()

    # Try to extract symbol, side, quantity, price
    # Patterns: "BUY 0.5 BTCUSDT @ 65000" or "BUY 0.5 BTC at $65,000"
    trade_match = re.search(
        r"(BUY|SELL)\s+([\d.]+)\s+([A-Z]{2,10}USDT|[A-Z]{2,10})\s+(?:at|@)\s+\$?([\d,]+\.?\d*)",
        remainder,
        re.IGNORECASE,
    )
    if trade_match:
        result["side"] = trade_match.group(1).upper()
        try:
            result["quantity"] = float(trade_match.group(2))
        except ValueError:
            pass
        result["symbol"] = trade_match.group(3).upper().replace("USDT", "/USDT")
        if not result["symbol"].endswith("/USDT"):
            result["symbol"] = result["symbol"] + "/USDT"
        try:
            result["price"] = float(trade_match.group(4).replace(",", ""))
        except ValueError:
            pass

    # PnL extraction
    pnl_match = re.search(r"(?:PnL|pnl)[:\s]+\$?(-?[\d,]+\.?\d*)", remainder)
    if pnl_match:
        try:
            result["pnl"] = float(pnl_match.group(1).replace(",", ""))
        except ValueError:
            pass

    return result
